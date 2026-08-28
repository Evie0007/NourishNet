import { Link, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import StorePage from "./pages/StorePage";
import PantryPage from "./pages/PantryPage";

function App() {
  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <nav className="flex items-center gap-6 border-b border-gray-200 bg-white px-6 py-4">
        <Link to="/" className="font-semibold">
          NourishNet
        </Link>
        <Link to="/store" className="text-sm text-gray-600 hover:text-gray-900">
          Store view
        </Link>
        <Link to="/pantry" className="text-sm text-gray-600 hover:text-gray-900">
          Pantry view
        </Link>
      </nav>

      <main className="mx-auto max-w-4xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/store" element={<StorePage />} />
          <Route path="/pantry" element={<PantryPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
