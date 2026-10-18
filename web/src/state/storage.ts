/**
 * The working session in IndexedDB: hundreds of megabytes are fine there, unlike sessionStorage (~5 MB, a few
 * thousand explained records). One key per value; every call fails soft so private mode or blocked storage only
 * means the session is not restored after a reload.
 */
const DB = "jana-address";
const STORE = "session";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await open();
  try {
    return await new Promise<T | undefined>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req ? req.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export async function loadValue<T>(key: string): Promise<T | undefined> {
  try { return await run<T>("readonly", (s) => s.get(key) as IDBRequest<T>); } catch { return undefined; }
}

export async function saveValue(key: string, value: unknown): Promise<void> {
  try { await run("readwrite", (s) => { s.put(value, key); }); } catch { /* storage blocked or full */ }
}

export async function deleteValue(key: string): Promise<void> {
  try { await run("readwrite", (s) => { s.delete(key); }); } catch { /* storage blocked */ }
}
