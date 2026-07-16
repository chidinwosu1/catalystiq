import { useEffect, useState } from "react";
import Header from "./components/Header";
import Disclaimer from "./components/Disclaimer";
import AnalysisCard from "./components/AnalysisCard";
import AnalysisCardSkeleton from "./components/AnalysisCardSkeleton";
import { mockReports } from "./mockData";

function App() {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 500);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <Header />

      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-xl font-semibold text-ink-primary">Watchlist</h1>
            <p className="mt-1 text-sm text-ink-secondary">
              Demo reports — the analytical and behavioral engines aren't wired up yet, so
              these are hand-authored samples of the Phase 7 report layout.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {loading
            ? Array.from({ length: 3 }, (_, i) => <AnalysisCardSkeleton key={i} />)
            : mockReports.map((report) => <AnalysisCard key={report.ticker} report={report} />)}
        </div>
      </main>

      <footer className="border-t border-border">
        <Disclaimer />
      </footer>
    </div>
  );
}

export default App;
