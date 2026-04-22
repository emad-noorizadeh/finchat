import { useNavigate, useParams } from 'react-router-dom'
import AgentBuilder from '../components/agents/AgentBuilder'

export default function AgentBuilderPage() {
  const navigate = useNavigate()
  const { agentName, channel } = useParams()

  return (
    <AgentBuilder
      agentName={agentName}
      channel={channel}
      onSave={() => navigate('/agents')}
      onCancel={() => navigate('/agents')}
    />
  )
}
