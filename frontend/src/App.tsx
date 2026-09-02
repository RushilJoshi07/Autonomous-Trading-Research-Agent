import { Route, Routes } from 'react-router-dom'
import { Layout } from './routes/Layout'
import { MandatePage } from './routes/MandatePage'
import { ChartersPage } from './routes/ChartersPage'
import { CharterDetailPage } from './routes/CharterDetailPage'
import { ScoreboardPage } from './routes/ScoreboardPage'
import { TraceDrilldownPage } from './routes/TraceDrilldownPage'

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<MandatePage />} />
        <Route path="charters" element={<ChartersPage />} />
        <Route path="charters/:charterId" element={<CharterDetailPage />} />
        <Route path="study-runs/:studyRunId/traces" element={<TraceDrilldownPage />} />
        <Route path="scoreboard" element={<ScoreboardPage />} />
      </Route>
    </Routes>
  )
}

export default App
