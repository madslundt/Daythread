import { createRoot } from "react-dom/client";
import App from "./App";
import { initializeNavigation } from "./routing";
import { initializeTheme } from "./theme";
import { initializeLanguage } from "./i18n";
import "./styles.css";
import "./today.css";

initializeTheme();
initializeLanguage();
initializeNavigation();
createRoot(document.getElementById("root")!).render(<App />);
