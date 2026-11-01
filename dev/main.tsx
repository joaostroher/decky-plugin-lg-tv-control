import { useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import createPlugin from "../src/index";
import "./style.css";

const plugin = createPlugin();
function App() {
  const [toast, setToast] = useState<{ title: ReactNode; body: ReactNode }>();
  useEffect(() => {
    const handler = (event: Event) => setToast((event as CustomEvent).detail);
    window.addEventListener("local-toast", handler);
    return () => window.removeEventListener("local-toast", handler);
  }, []);
  return <main><header><span className="badge">LOCAL DEVELOPMENT</span>
    <h1>LG TV Control</h1><p>Control your real TV over the network. SteamOS suspend/resume automation is disabled.</p></header>
    {toast && <div className="toast" role="status"><strong>{toast.title}</strong><div>{toast.body}</div>
      <button onClick={() => setToast(undefined)}>Dismiss</button></div>}
    <div className="panel">{plugin.content}</div></main>;
}
createRoot(document.getElementById("root")!).render(<App />);
