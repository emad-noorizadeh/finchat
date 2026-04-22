import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AppLayout from './components/layout/AppLayout'
import LoginPage from './pages/LoginPage'
import ChatPage from './pages/ChatPage'
import ToolsPage from './pages/ToolsPage'
import FilesPage from './pages/FilesPage'
import WidgetsPage from './pages/WidgetsPage'
import AgentsPage from './pages/AgentsPage'
import AgentBuilderPage from './pages/AgentBuilderPage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LoginPage />} />
        {/* Full-page builder — no sidebar */}
        <Route path="/agents/builder" element={<AgentBuilderPage />} />
        <Route path="/agents/builder/:agentName/:channel" element={<AgentBuilderPage />} />
        {/* Main layout with sidebar */}
        <Route element={<AppLayout />}>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/knowledge" element={<FilesPage />} />
          <Route path="/widgets" element={<WidgetsPage />} />
          <Route path="/agents" element={<AgentsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
