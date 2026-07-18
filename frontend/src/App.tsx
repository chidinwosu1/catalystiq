import { useState } from "react";
import Header from "./components/Header";
import Disclaimer from "./components/Disclaimer";
import HomePage from "./pages/HomePage";
import TradeCenterPage from "./pages/TradeCenterPage";
import TradeTicketPage from "./pages/TradeTicketPage";
import PortfolioPage from "./pages/PortfolioPage";
import MarketIntelligencePage from "./pages/MarketIntelligencePage";
import AnalysisJournalPage from "./pages/AnalysisJournalPage";
import type { PageId } from "./types/nav";

function App() {
  const [activePage, setActivePage] = useState<PageId>("home");
  const [tradeSymbol, setTradeSymbol] = useState("");
  const [analysisSymbol, setAnalysisSymbol] = useState("");

  // The order ticket ("Confirm Trade" step) lives on its own route, reached
  // from an opportunity, from Investment Strategy, or from a ticker search.
  function goToTicket(symbol: string) {
    setTradeSymbol(symbol);
    setActivePage("ticket");
  }

  function goToAnalysis(symbol: string) {
    setAnalysisSymbol(symbol);
    setActivePage("analysis");
  }

  return (
    <div className="flex min-h-screen flex-col">
      <Header activePage={activePage} onNavigate={setActivePage} onSearch={goToTicket} />

      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        {activePage === "home" && (
          <HomePage onNavigate={setActivePage} onViewAnalysis={goToAnalysis} />
        )}
        {activePage === "trade" && (
          <TradeCenterPage
            onTrade={goToTicket}
            onViewAnalysis={goToAnalysis}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "ticket" && (
          <TradeTicketPage
            initialSymbol={tradeSymbol}
            onViewAnalysis={goToAnalysis}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "portfolio" && (
          <PortfolioPage
            onTrade={goToTicket}
            onViewAnalysis={goToAnalysis}
            onNavigate={setActivePage}
          />
        )}
        {activePage === "markets" && (
          <MarketIntelligencePage onTrade={goToTicket} onViewAnalysis={goToAnalysis} />
        )}
        {activePage === "analysis" && (
          <AnalysisJournalPage
            initialSymbol={analysisSymbol}
            onTrade={goToTicket}
            onNavigate={setActivePage}
          />
        )}
      </main>

      <footer className="border-t border-border">
        <Disclaimer />
      </footer>
    </div>
  );
}

export default App;
