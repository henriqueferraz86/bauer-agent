import { useEffect, useRef } from "react";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (options: { client_id: string; callback: (response: { credential: string }) => void }) => void;
          renderButton: (element: HTMLElement, options: Record<string, string | number>) => void;
        };
      };
    };
  }
}

let googleScriptPromise: Promise<void> | null = null;

function loadGoogleScript(): Promise<void> {
  if (window.google?.accounts.id) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  googleScriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-bauer-google="true"]');
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("Falha ao carregar Google Identity Services.")), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.dataset.bauerGoogle = "true";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Falha ao carregar Google Identity Services."));
    document.head.appendChild(script);
  });
  return googleScriptPromise;
}

export default function GoogleButton({
  clientId,
  onCredential,
  onError,
}: {
  clientId: string;
  onCredential: (credential: string) => void | Promise<void>;
  onError?: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const callback = useRef(onCredential);
  callback.current = onCredential;

  useEffect(() => {
    let active = true;
    loadGoogleScript()
      .then(() => {
        if (!active || !container.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: ({ credential }) => callback.current(credential),
        });
        container.current.replaceChildren();
        window.google.accounts.id.renderButton(container.current, {
          theme: "outline",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          width: 320,
        });
      })
      .catch((error) => onError?.(String(error)));
    return () => { active = false; };
  }, [clientId, onError]);

  return <div className="google-signin" ref={container} />;
}
