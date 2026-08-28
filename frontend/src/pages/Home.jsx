import { Link } from "react-router-dom";

export default function Home() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">NourishNet</h1>
      <p className="text-gray-600">
        Pick a view to get started. This talks directly to the FastAPI
        backend running at the API URL set in <code>.env</code>.
      </p>
      <div className="flex gap-4">
        <Link
          to="/store"
          className="rounded-lg bg-gray-900 px-4 py-2 text-white hover:bg-gray-700"
        >
          Store view
        </Link>
        <Link
          to="/pantry"
          className="rounded-lg border border-gray-300 px-4 py-2 hover:bg-gray-100"
        >
          Pantry view
        </Link>
      </div>
    </div>
  );
}
