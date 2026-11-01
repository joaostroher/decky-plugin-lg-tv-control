import type { ReactNode } from "react";

export function callable<Arguments extends unknown[], Result>(method: string) {
  return async (...args: Arguments): Promise<Result> => {
    const response = await fetch(`/rpc/${method}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "Local backend unavailable.");
    return data.result;
  };
}

export const definePlugin = <T,>(factory: () => T) => factory;
export const toaster = {
  toast({ title, body }: { title: ReactNode; body: ReactNode }) {
    window.dispatchEvent(new CustomEvent("local-toast", { detail: { title, body } }));
  },
};
