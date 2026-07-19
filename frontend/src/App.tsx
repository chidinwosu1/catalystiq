import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import Header from "./components/Header";
import Disclaimer from "./components/Disclaimer";
import LoginScreen from "./components/LoginScreen";
import HomePage from "./pages/HomePage";
import TradeTicketPage from "./pages/TradeTicketPage";
import PortfolioPage from "./pages/PortfolioPage";
import MarketIntelligencePage from "./pages/MarketIntelligencePage";
import AnalysisJournalPage from "./pages/AnalysisJournalPage";
import DataSourcesPage from "./pages/DataSourcesPage";
import { getSession, logout } from "./lib/api";
import type { PageId } from "./types/nav";

type AuthState = "checking" | "out" | "in";

function App() {
  const [auth, setAuth] = useState<AuthState>("checking");
  const [activePage, setActivePage] = useState<PageId>("home");
  const [tradeSymbol, setTradeSymbol] = useState("");
  const [analysisSymbol, setAnalysisSymbol] = useState("");

  useEffect(() => {
    getSession()
      .then((s) => setAuth(s.authenticated ? "in" : "out"))
      .catch(() => setAuth("out"));
  }, []);

  async function handleSignOut() {
    try {
      await logout();
    } catch {
      /* clearing client state below regardless */
    }
    setAuth("out");
    setActivePage("home");
  }

  function goToTrade(symbol: string) {
    setTradeSymbol(symbol);
    setActivePage("trade");
  }

  function goToAnalysis(symbol: string) {
    setAnalysisSymbol(symbol);
    setActivePage("analysis");
  }

  if (auth === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center text-ink-secondary">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  if (auth === "out") {
    return <LoginScreen onSuccess={() => setAuth("in")} />;
  }

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        activePage={activePage}
        onNavigate={setActivePage}
        onSearch={goToTrade}
        onSignOut={handleSignOut}
      />

      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        {activePage === "home" && (
          <HomePage onNavigate={setActivePage} onViewAnalysis={goToAnalysis} />
        )}
        {activePage === "trade" && (
          <TradeTicketPage
            initialSymbol={tradeSymbol}
            onViewAnalysis={goToAnalysis}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "portfolio" && (
          <PortfolioPage
            onTrade={goToTrade}
            onViewAnalysis={goToAnalysis}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "markets" && (
          <MarketIntelligencePage onTrade={goToTrade} onViewAnalysis={goToAnalysis} />
        )}
        {activePage === "analysis" && (
          <AnalysisJournalPage
            initialSymbol={analysisSymbol}
            onTrade={goToTrade}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "data-sources" && <DataSourcesPage />}
      </main>

      <footer className="border-t border-border">
        <Disclaimer />
      </footer>
    </div>
  );
}

export default App;
