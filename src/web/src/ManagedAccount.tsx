import { createContext, Fragment, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { displayLocale, t } from "./i18n";
import { isManagedOrigin, managedArchiveAvailable, managedServiceUrl, ManagedSession, type ManagedSnapshot } from "./managedService";
import { Loading } from "./UI";
import { Icon } from "./Icon";
import "./managedAccount.css";

type AccountContext = ManagedSnapshot & { refresh: () => void; signOut: () => void };
const Context = createContext<AccountContext>({ managed: false, status: "local", refresh: () => {}, signOut: () => {} });

export function ManagedAccountProvider({ children }: { children: ReactNode }) {
  const [session] = useState(() => new ManagedSession());
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  useEffect(() => {
    if (!isManagedOrigin()) return;
    void session.refresh();
    const timer = window.setInterval(() => void session.refresh(), 30_000);
    const focus = () => { void session.refresh(); };
    window.addEventListener("focus", focus);
    window.addEventListener("omi:archive-changed", focus);
    window.addEventListener("daythread:session-expired", session.expire);
    return () => {
      session.dispose();
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
      window.removeEventListener("omi:archive-changed", focus);
      window.removeEventListener("daythread:session-expired", session.expire);
    };
  }, [session]);
  return <Context.Provider value={{ ...snapshot, refresh: session.refresh, signOut: session.signOut }}>{children}</Context.Provider>;
}

export const useManagedAccount = () => useContext(Context);

function creditLabel(units: number): string {
  const amount = new Intl.NumberFormat(displayLocale(), { maximumFractionDigits: 3 }).format(units / 1000);
  return t("{value0} credits", { value0: amount });
}

export function ManagedAccountBadge({ placement }: { placement: "sidebar" | "header" }) {
  const session = useManagedAccount();
  if (!session.managed) return placement === "sidebar"
    ? <><a className="managed-signin-link" href={managedServiceUrl("/pricing")}>{t("Plans & pricing")}</a>
      <a className="managed-signin-link" href={managedServiceUrl("/signin")}>{t("Sign in to Daythread")}</a></> : null;
  if (!session.account) return null;
  return <a className={`managed-account-badge managed-account-${placement}`} href={managedServiceUrl("/account")}
    aria-label={t("Manage account · {value0}", { value0: creditLabel(session.account.credits.available_units) })}>
    <span>{creditLabel(session.account.credits.available_units)}</span>
    {placement === "sidebar" ? <small>{t("Manage account")}</small> : null}
  </a>;
}

export function ManagedAccountPanel() {
  const session = useManagedAccount(), account = session.account;
  return <section className="managed-account-panel" aria-labelledby="managed-account-title">
    <div className="settings-section-heading"><h2 id="managed-account-title">{t("Daythread account")}</h2>
      <p>{session.managed ? t("Your hosting, credits, and AI connections in one place.") : t("Optional managed hosting")}</p>
    </div>
    {!session.managed ? <>
      <p>{t("Sign in for managed hosting, or keep using your own backend. Your current archive stays on this server.")}</p>
      <a className="button accent" href={managedServiceUrl("/signin")}>{t("Sign in to Daythread")}</a>
    </> : session.status === "loading" ? <Loading>{t("Loading your account…")}</Loading>
      : account ? <>
        <p className="managed-account-name"><strong>{account.name}</strong><span className="muted">{account.email}</span></p>
        <dl className="managed-account-stats">
          <div><dt>{t("Available credits")}</dt><dd>{creditLabel(account.credits.available_units)}</dd></div>
          <div><dt>{t("Hosting")}</dt><dd>{account.hosting_active ? t("Active") : t("Not active")}</dd></div>
        </dl>
        {account.hosted_until ? <p className="caption">{t("Hosting paid through {value0}", { value0: new Intl.DateTimeFormat(displayLocale(), { dateStyle: "medium" }).format(account.hosted_until * 1000) })}</p> : null}
        <p>{t("Manage your subscription, buy credits, or choose your own AI provider in your account.")}</p>
        <div className="button-row"><a className="button accent" href={managedServiceUrl("/account")}>{t("Manage account")}</a>
          <button className="button" type="button" onClick={session.signOut}>{t("Sign out")}</button></div>
      </> : <>
        <p role="status">{session.signingOut ? t("Signing out…") : session.status === "signed-out"
          ? t("Your session has ended. Sign in to continue.") : t("Your account could not be loaded. Check your connection and try again.")}</p>
        <div className="button-row"><a className="button accent" href={managedServiceUrl("/signin")}>{t("Sign in to Daythread")}</a>
          {session.status === "unavailable" ? <button className="button" type="button" onClick={session.refresh}>{t("Try again")}</button> : null}</div>
    </>}
    <p><a className="text-link" href={managedServiceUrl("/pricing")}>{t("Compare plans & credit pricing")}<Icon name="chevron-right" size={15} /></a></p>
  </section>;
}

export function ManagedConnections() {
  return <section aria-label={t("Recording & devices")}>
    <p>{t("Sign in to Daythread in the iPhone app to sync recordings with your hosted archive.")}</p>
    <a className="button" href={managedServiceUrl("/account")}>{t("Manage account")}</a>
  </section>;
}

export function ManagedArchiveGate({ children }: { children: ReactNode }) {
  const session = useManagedAccount();
  return session.managed && (session.status !== "signed-in" || !managedArchiveAvailable(session.account))
    ? <main className="managed-session-screen"><a className="managed-session-brand" href={managedServiceUrl("/account")}>{t("Daythread")}</a>
      <ManagedAccountPanel /></main> : <Fragment key={session.account?.id ?? "self-hosted"}>{children}</Fragment>;
}
