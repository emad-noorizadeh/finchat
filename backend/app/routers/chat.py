import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session, get_chroma_client
from app.models.chat import ChatSession, Message
from app.models.widget_instance import WidgetInstance
from app.services.widget_service import WidgetService
from app.models.stream_events import (
    thinking_event, tool_start_event, tool_complete_event,
    response_chunk_event, response_event, interrupt_event,
    error_event, done_event, widget_event,
)
from app.schemas.chat import CreateSessionRequest, CreateSessionResponse
from app.services.memory import MemoryService
from app.agent.graph import build_agent_graph
from app.agent.checkpointer import get_checkpointer
from app.log import LogContextManager, generate_request_id

router = APIRouter(prefix="/api/chat", tags=["chat"])


def sse(event) -> str:
    """Format a StreamEvent as an SSE data line."""
    return f"data: {event.model_dump_json()}\n\n"


class SendMessageRequest(BaseModel):
    content: str = ""
    user_id: str
    type: str = "message"
    data: dict | None = None
    channel: str = "chat"


class QuickActionRequest(BaseModel):
    user_id: str
    action_id: str
    channel: str = "chat"


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest, session: Session = Depends(get_session)):
    chat = ChatSession(user_id=req.user_id)
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return CreateSessionResponse(session_id=chat.id, title=chat.title)


@router.get("/sessions")
def list_sessions(user_id: str, session: Session = Depends(get_session)):
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = session.exec(stmt).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: str,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
        .offset(offset)
        .limit(limit)
    )
    messages = session.exec(stmt).all()

    # Batch-fetch widget instances for widget messages
    ws = WidgetService(session)
    widget_ids = [m.content for m in messages if m.message_type == "widget" and m.content]
    widget_map = ws.batch_fetch(widget_ids) if widget_ids else {}

    result = []
    for m in messages:
        m_dict = {
            "id": m.id,
            "role": m.role,
            "message_type": m.message_type,
            "content": m.content,
            "tool_calls": m.tool_calls,
            "tool_call_id": m.tool_call_id,
            "channel": m.channel,
            "created_at": m.created_at.isoformat(),
        }
        # Inject widget data from instance (latest status)
        if m.message_type == "widget" and m.content in widget_map:
            m_dict["widget"] = WidgetService.instance_to_dict(widget_map[m.content])
        result.append(m_dict)

    return result


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    session: Session = Depends(get_session),
):
    chat = session.get(ChatSession, session_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    memory = MemoryService(session, get_chroma_client())

    if req.type == "message" and req.content:
        memory.save_message(session_id, "user", req.content, channel=req.channel)
        if chat.title == "New Chat":
            chat.title = req.content[:50] + ("..." if len(req.content) > 50 else "")
            session.add(chat)
            session.commit()

    async def event_stream():
        from langchain_core.messages import HumanMessage
        from langgraph.types import Command

        # Base RunnableConfig — carries thread_id for checkpointing AND the
        # LangSmith trace tagging (run_name / tags / metadata). The trace
        # fields are no-ops when tracing is disabled.
        from app.observability import trace_config
        config = trace_config(
            run_name=f"chat.{req.type}",
            tags=[req.channel, f"user:{req.user_id}"],
            metadata={
                "user_id":    req.user_id,
                "session_id": session_id,
                "channel":    req.channel,
                "type":       req.type,
            },
            thread_id=session_id,
        )
        accumulated_content = ""
        final_emitted = False

        turn_id = generate_request_id()
        log_ctx = LogContextManager(
            session_id=session_id,
            user_id=req.user_id,
            channel=req.channel,
            turn_id=turn_id,
            operation=f"chat:{req.type}",
        )
        log_ctx.__enter__()

        # Start per-turn metrics accumulator (llm_calls, tool_calls, iterations,
        # latencies). Nodes bump counters; we emit the summary at stream end.
        from app.agent.nodes import reset_turn_metrics, emit_turn_summary
        reset_turn_metrics()
        turn_exit_reason = "unknown"

        # Time-to-first-visible-output — separates "model thinking" from
        # "network buffering". Measured from request arrival to first
        # user-visible event (response_chunk, widget, or final_response).
        # Logged once per turn as [ttft] on first emission.
        import time as _ttft_time
        import logging as _ttft_log
        _ttft_start = _ttft_time.perf_counter()
        _ttft_fired = {"done": False}

        def _log_ttft(event_kind: str):
            if _ttft_fired["done"]:
                return
            _ttft_fired["done"] = True
            _ttft_log.getLogger("app.routers.chat").info(
                "[ttft] duration_ms=%.0f first_event=%s",
                (_ttft_time.perf_counter() - _ttft_start) * 1000,
                event_kind,
            )

        try:
            async with get_checkpointer() as checkpointer:
                compiled = build_agent_graph(checkpointer=checkpointer)

                if req.type == "resume":
                    snapshot = await compiled.aget_state(config)
                    _pending = bool(snapshot.next) or any(
                        getattr(t, "interrupts", None) for t in (snapshot.tasks or ())
                    )
                    if not _pending:
                        yield sse(error_event("No pending confirmation to resume."))
                        return

                    # Update confirmation widget status as side effect
                    widget_instance_id = (req.data or {}).get("widget_instance_id")
                    if widget_instance_id:
                        wsi = WidgetService(session)
                        confirmed = (req.data or {}).get("confirmed", False)
                        wsi.update_status(
                            widget_instance_id,
                            "completed" if confirmed else "dismissed",
                        )

                    # Stream events (same as fresh-message path) so widgets,
                    # glass, and chunks surface in real time. astream_events
                    # accepts Command(resume=...) via the second arg.
                    async for event in compiled.astream_events(
                        Command(resume=req.data), config=config, version="v2"
                    ):
                        kind = event.get("event", "")
                        if kind == "on_custom_event" and event.get("name") == "widget":
                            widget_data = event.get("data")
                            if widget_data:
                                _log_ttft("widget")
                                yield sse(widget_event(widget_data))
                                instance_id = widget_data.get("instance_id", "")
                                memory_svc = MemoryService(session, get_chroma_client())
                                memory_svc.save_message(
                                    session_id, "assistant",
                                    content=instance_id or json.dumps(widget_data),
                                    message_type="widget",
                                    channel=req.channel,
                                )
                            continue
                        if kind == "on_custom_event" and event.get("name") == "final_response":
                            final_data = event.get("data") or {}
                            text = final_data.get("content", "")
                            if text:
                                accumulated_content = text
                                _log_ttft("final_response")
                                yield sse(response_event(accumulated_content))
                                final_emitted = True
                            continue
                        # Per-tool activity (dispatched from tool_execute). We
                        # hook custom events rather than on_tool_start because
                        # our tools aren't LangChain BaseTools — LangGraph
                        # doesn't emit on_tool_start for direct-Python calls.
                        if kind == "on_custom_event" and event.get("name") == "tool_activity":
                            d = event.get("data") or {}
                            if d.get("phase") == "start":
                                yield sse(tool_start_event(
                                    d.get("name", ""),
                                    tool_args=d.get("args"),
                                    label=d.get("label"),
                                ))
                            elif d.get("phase") == "end":
                                yield sse(tool_complete_event(
                                    d.get("name", ""),
                                    d.get("preview", ""),
                                ))
                            continue
                        if kind == "on_chat_model_stream":
                            # Skip sub-agent internal LLM calls (e.g. parse_node
                            # structured-output calls) — their raw JSON would
                            # leak into the user-visible chat stream.
                            if "subagent_internal" in (event.get("tags") or []):
                                continue
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                content = chunk.content
                                if isinstance(content, str) and content:
                                    accumulated_content += content
                                    _log_ttft("response_chunk")
                                    yield sse(response_chunk_event(content))
                                    from app.agent.nodes import current_turn_metrics
                                    _mm = current_turn_metrics()
                                    if _mm is not None:
                                        _mm["prose_emitted"] = True
                else:
                    input_state = {
                        "messages": [HumanMessage(content=req.content)],
                        "user_id": req.user_id,
                        "session_id": session_id,
                        "available_tools": [],
                        "tool_schemas": [],
                        "iteration_count": 0,
                        "enrichment_context": "",
                        "base_system_prompt": "",
                        "knowledge_sources": [],
                        "search_tool_calls": 0,
                        "channel": req.channel,
                        "response_terminated": False,
                        "last_executed_tools": [],
                        "variables": {},
                        "hop_guard_triggered": False,
                    }

                    yield sse(thinking_event("Understanding your request..."))

                    async for event in compiled.astream_events(
                        input_state, config=config, version="v2"
                    ):
                        kind = event.get("event", "")

                        # Widget events from dispatch_custom_event inside tool_node
                        if kind == "on_custom_event" and event.get("name") == "widget":
                            widget_data = event.get("data")
                            if widget_data:
                                _log_ttft("widget")
                                yield sse(widget_event(widget_data))
                                # Save message with instance_id as content (not full JSON)
                                instance_id = widget_data.get("instance_id", "")
                                memory_svc = MemoryService(session, get_chroma_client())
                                memory_svc.save_message(
                                    session_id, "assistant",
                                    content=instance_id or json.dumps(widget_data),
                                    message_type="widget",
                                    channel=req.channel,
                                )
                            continue

                        # Final response emitted directly by a tool (glass path)
                        if kind == "on_custom_event" and event.get("name") == "final_response":
                            final_data = event.get("data") or {}
                            text = final_data.get("content", "")
                            if text:
                                accumulated_content = text
                                _log_ttft("final_response")
                                yield sse(response_event(accumulated_content))
                                final_emitted = True
                            continue

                        # Per-tool activity (dispatched from tool_execute).
                        # See app/agent/nodes.py:run_one — our tools aren't
                        # LangChain BaseTools, so on_tool_start doesn't fire
                        # for direct-Python tool_execute calls. We dispatch a
                        # custom `tool_activity` event instead.
                        if kind == "on_custom_event" and event.get("name") == "tool_activity":
                            d = event.get("data") or {}
                            if d.get("phase") == "start":
                                yield sse(tool_start_event(
                                    d.get("name", ""),
                                    tool_args=d.get("args"),
                                    label=d.get("label"),
                                ))
                            elif d.get("phase") == "end":
                                yield sse(tool_complete_event(
                                    d.get("name", ""),
                                    d.get("preview", ""),
                                ))
                            continue

                        if kind == "on_chat_model_stream":
                            # Skip sub-agent internal LLM calls (e.g. parse_node
                            # structured-output calls) — their raw JSON would
                            # leak into the user-visible chat stream.
                            if "subagent_internal" in (event.get("tags") or []):
                                continue
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                content = chunk.content
                                if isinstance(content, str) and content:
                                    accumulated_content += content
                                    _log_ttft("response_chunk")
                                    yield sse(response_chunk_event(content))
                                    from app.agent.nodes import current_turn_metrics
                                    _mm = current_turn_metrics()
                                    if _mm is not None:
                                        _mm["prose_emitted"] = True

                # After stream ends — check if any task has a pending interrupt.
                # Note: snapshot.next may be empty even when an interrupt is
                # pending inside a tool call on the resume path (LangGraph
                # records the interrupt on the task but doesn't populate next
                # the same way as a fresh pause). We trust tasks[*].interrupts
                # as the authoritative signal.
                snapshot = await compiled.aget_state(config)
                interrupt_value = None
                for task in (snapshot.tasks or ()):
                    ints = getattr(task, "interrupts", None) or ()
                    if ints:
                        interrupt_value = ints[0].value
                        break
                if interrupt_value:
                    yield sse(interrupt_event(interrupt_value))
                    return

                # Append knowledge sources if available — suppress in voice (markdown is bad for TTS)
                knowledge_sources = snapshot.values.get("knowledge_sources", [])
                if knowledge_sources and accumulated_content and req.channel != "voice":
                    sources_block = "\n\n**Sources**\n" + "\n".join(
                        f"- [{s['title']}]({s['url']})" for s in knowledge_sources
                    )
                    accumulated_content += sources_block
                    yield sse(response_chunk_event(sources_block))

            # Save complete assistant message to DB
            if accumulated_content:
                memory_svc = MemoryService(session, get_chroma_client())
                memory_svc.save_message(
                    session_id, "assistant", accumulated_content,
                    channel=req.channel,
                )

            if not final_emitted:
                yield sse(response_event(accumulated_content))
            yield sse(done_event())
            turn_exit_reason = "text" if accumulated_content and not final_emitted else "terminal"

        except Exception as e:
            yield sse(error_event(str(e)))
            turn_exit_reason = f"error:{type(e).__name__}"
        finally:
            emit_turn_summary(
                exit_reason=turn_exit_reason,
                session_id=session_id, user_id=req.user_id, turn_id=turn_id,
            )
            log_ctx.__exit__(None, None, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/quick_action")
async def quick_action(
    session_id: str,
    req: QuickActionRequest,
    session: Session = Depends(get_session),
):
    """Execute a canned data-fetch recipe with ZERO LLM calls.

    Shape:
      1. Save the action's user-message text so chat history reads naturally.
      2. Run the action's data tool directly in Python.
      3. Build the mapped widget via the catalog, persist a WidgetInstance.
      4. Stream back one widget SSE event + done.

    Used by the new-chat quick-action buttons. Interactive sub-agent flows
    (transfer, refund) still go through the normal /messages path — they
    need the Planner. See app/services/quick_actions.py for the registry.
    """
    from app.services.quick_actions import get_action, build_widget_for_action
    from app.tools import get_tool

    chat = session.get(ChatSession, session_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    action = get_action(req.action_id)
    if not action:
        raise HTTPException(status_code=400, detail=f"Unknown quick action: {req.action_id}")

    tool = get_tool(action["tool"])
    if not tool:
        raise HTTPException(status_code=500, detail=f"Tool not registered: {action['tool']}")

    from app.tools.base import ToolResult

    async def event_stream():
        # Session rehydrate — mirrors app/agent/nodes.py:enrich(). Needed
        # because the quick-action path bypasses the graph entirely, so
        # the in-memory profile/transaction caches never get populated
        # after a backend restart.
        from app.services import profile_service
        from app.services.transaction_service import load_transactions
        if req.user_id and not profile_service.is_loaded(req.user_id):
            try:
                profile_service.load_profile(req.user_id)
                prefix = profile_service.get_file_prefix(req.user_id)
                if prefix:
                    load_transactions(req.user_id, prefix)
            except Exception:
                pass  # tool will surface its own "not loaded" error

        # Persist the user message FIRST so history shows what was clicked.
        memory = MemoryService(session, get_chroma_client())
        memory.save_message(session_id, "user", action["message"], channel=req.channel)

        # Title the session from the clicked action on first turn.
        if chat.title == "New Chat":
            chat.title = action["message"][:50]
            session.add(chat)
            session.commit()

        # Run the tool directly. context mirrors what tool_execute builds;
        # render-tool machinery isn't needed here (no slot lookup).
        context = {
            "user_id": req.user_id,
            "session_id": session_id,
            "channel": req.channel,
            "available_tools": [],
            "search_tool_calls": 0,
            "variables": {},
        }
        try:
            result = await tool.execute(action["args"], context)
        except Exception as e:  # noqa: BLE001
            yield sse(error_event(f"Quick action failed: {e}"))
            yield sse(done_event())
            return

        if not isinstance(result, ToolResult):
            yield sse(error_event("Quick action tool did not return a ToolResult."))
            yield sse(done_event())
            return

        # Prefer slot_data (full render payload); fall back to parsed to_llm.
        data = result.slot_data
        if data is None:
            try:
                data = json.loads(result.to_llm)
            except (json.JSONDecodeError, TypeError):
                yield sse(error_event("Quick action produced no usable data."))
                yield sse(done_event())
                return

        # Build + persist widget.
        widget_payload = build_widget_for_action(req.action_id, data)
        # Stamp user/session into metadata so widget-action handlers
        # (if any) can find the right context later.
        meta = dict(widget_payload.get("metadata") or {})
        meta.setdefault("user_id", req.user_id)
        meta.setdefault("session_id", session_id)
        widget_payload["metadata"] = meta
        try:
            wsvc = WidgetService(session)
            instance = wsvc.create_instance(
                session_id=session_id,
                widget_data=widget_payload,
                created_by=action["created_by"],
            )
            widget_payload = {**widget_payload, "instance_id": instance.id, "status": "pending"}
        except Exception:
            pass  # emit un-stamped rather than dropping the widget

        # Persist + emit.
        memory.save_message(
            session_id, "assistant",
            content=widget_payload.get("instance_id", "") or json.dumps(widget_payload),
            message_type="widget",
            channel=req.channel,
        )
        yield sse(widget_event(widget_payload))
        yield sse(done_event())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, session: Session = Depends(get_session)):
    chat = session.get(ChatSession, session_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete widget instances
    ws = WidgetService(session)
    ws.delete_for_session(session_id)

    # Delete messages
    msgs = session.exec(select(Message).where(Message.session_id == session_id)).all()
    for m in msgs:
        session.delete(m)

    session.delete(chat)
    session.commit()
    return {"status": "deleted"}
