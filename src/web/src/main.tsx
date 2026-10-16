import { createRoot } from "react-dom/client";
import App from "./App";
import { initializeNavigation } from "./routing";
import { ImportanceProvider } from "./Importance";
import { initializeTheme } from "./theme";
import "./styles.css";
import "./today.css";

initializeTheme();
initializeNavigation();
createRoot(document.getElementById("root")!).render(<ImportanceProvider><App /></ImportanceProvider>);
