import { useCallback, useEffect, useState, type FormEvent } from "react";
import { aiStatus, type AiStatus } from "../api";
import { saveKey, savedKey } from "../lib/ai";

/**
 * Bring your own Claude key. The key lives in this browser's localStorage only and goes to the API in a request header
 * (never a URL) when the provider is Claude; the API uses it for that one request and forgets it. With a server-side
 * key (ANTHROPIC_API_KEY on the API) nothing has to be entered — the status line says so. The jury image has no AI
 * tooling at all, so this never shows there.
 */
export function AiKeyField({ provider, compact = false }: { provider: string; compact?: boolean }) {
  const [{ apiKey, workspaceId }, setStored] = useState(savedKey);
  const [draft, setDraft] = useState({ apiKey, workspaceId });
  const [status, setStatus] = useState<AiStatus | null | false>(null);   // false: the API is down or has no AI tooling
  const [open, setOpen] = useState(!compact);

  const refresh = useCallback(() => { aiStatus().then(setStatus).catch(() => setStatus(false)); }, []);
  useEffect(() => { if (provider === "anthropic") refresh(); }, [provider, refresh, apiKey, workspaceId]);
  useEffect(() => { if (compact && status && status.credentials === "none" && !apiKey) setOpen(true); }, [compact, status, apiKey]);

  if (provider !== "anthropic") return null;

  const source = status ? status.credentials : null;
  const userScoped = draft.apiKey.trim().startsWith("sk-ant-usr-");
  const line = status === false ? "сервер не отвечает"
    : source === "request" ? `ключ из этого браузера (…${apiKey.slice(-4)})`
    : source === "server" ? "ключ задан на сервере"
    : source === "none" ? "ключа нет — вставьте свой, он останется в этом браузере"
    : "проверяю…";
  const dirty = draft.apiKey.trim() !== apiKey || draft.workspaceId.trim() !== workspaceId;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    saveKey(draft.apiKey, draft.workspaceId);
    setStored(savedKey());
    if (compact) setOpen(false);
  };
  const clear = () => {
    saveKey("", "");
    setDraft({ apiKey: "", workspaceId: "" });
    setStored(savedKey());
  };

  return (
    <div className={`ai-key${compact ? " is-compact" : ""}`}>
      <p className="ai-key-line">
        <span className={`ai-key-dot${source === "none" || status === false ? " is-flag" : source ? " is-ok" : ""}`} aria-hidden="true" />
        <span>Ключ Anthropic: {line}</span>
        {compact && (
          <button type="button" className="link-btn" onClick={() => setOpen((v) => !v)}>{open ? "Скрыть" : apiKey ? "Изменить" : "Вставить"}</button>
        )}
      </p>
      {open && (
        <form className="ai-key-form" onSubmit={submit}>
          <label className="inline-field">
            <span>Ключ</span>
            <input type="password" autoComplete="off" spellCheck={false} placeholder="sk-ant-…" value={draft.apiKey}
              onChange={(e) => setDraft((d) => ({ ...d, apiKey: e.target.value }))} aria-label="Ключ Anthropic API" />
          </label>
          {(userScoped || draft.workspaceId) && (
            <label className="inline-field">
              <span>Workspace</span>
              <input autoComplete="off" spellCheck={false} placeholder="wrkspc_…" value={draft.workspaceId}
                onChange={(e) => setDraft((d) => ({ ...d, workspaceId: e.target.value }))} aria-label="Anthropic Workspace ID" />
            </label>
          )}
          <button type="submit" className="btn btn-ghost btn-sm" disabled={!dirty}>Сохранить</button>
          {apiKey && <button type="button" className="btn btn-ghost btn-sm" onClick={clear}>Удалить</button>}
          <p className="ai-key-note">
            Хранится только в этом браузере и уходит на API в заголовке запроса; сервер его не сохраняет и не логирует.
            {userScoped && " Ключ sk-ant-usr-… привязан к пользователю: укажите Workspace ID (Console → Settings → Workspaces)."}
          </p>
        </form>
      )}
    </div>
  );
}
