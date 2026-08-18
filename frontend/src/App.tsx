import { Route, Routes } from 'react-router-dom'
import Browse from './pages/Browse'
import Detail from './pages/Detail'
import Settings from './pages/Settings'
import Categories from './pages/Categories'

export default function App() {
  return (
    <div className="app-shell">
      <Routes>
        <Route path="/" element={<Browse />} />
        <Route path="/documents/:id" element={<Detail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/categories" element={<Categories />} />
      </Routes>
    </div>
  )
}
