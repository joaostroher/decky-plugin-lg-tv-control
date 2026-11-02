import { useEffect, type ReactNode, type CSSProperties, type ChangeEvent } from "react";
import { createRoot } from "react-dom/client";

type Common = { children?: ReactNode; style?: CSSProperties; className?: string };
export const staticClasses = { Title: "plugin-title" };
export const findSP = (): Window | undefined => undefined;
export const QuickAccessTab = { Decky: 999 };
export const Navigation = { OpenQuickAccessMenu(_tab: number) {} };
export const PanelSection = ({ title, children }: Common & { title?: string }) =>
  <section>{title && <h2>{title}</h2>}{children}</section>;
export const PanelSectionRow = ({ children }: Common) => <div className="row">{children}</div>;
export const DialogHeader = ({ children }: Common) => <h2>{children}</h2>;
export const DialogBodyText = ({ children }: Common) => <p>{children}</p>;
export const DialogControlsSection = ({ children, style }: Common) => <div style={style}>{children}</div>;
export const DialogButton = ({ children, style, disabled, onClick }: Common & {
  disabled?: boolean; onClick?: () => void;
}) => <button style={style} disabled={disabled} onClick={onClick}>{children}</button>;
export const ButtonItem = ({ children, disabled, onClick }: Common & {
  layout?: string; disabled?: boolean; onClick?: () => void;
}) => <button disabled={disabled} onClick={onClick}>{children}</button>;
export const TextField = ({ label, value, disabled, onChange }: {
  label: string; value: string; disabled?: boolean; onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) => <label>{label}<input value={value} disabled={disabled} onChange={onChange} /></label>;
export const ToggleField = ({ label, description, checked, disabled, onChange }: {
  label: string; description?: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void;
}) => <label className="toggle"><span>{label}{description && <small>{description}</small>}</span>
  <input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /></label>;
export const DropdownItem = ({ label, selectedOption, rgOptions, disabled, onChange }: {
  label: string; selectedOption: string; rgOptions: { label: string; data: string }[];
  disabled?: boolean; onChange: (option: { data: string }) => void;
}) => <label>{label}<select value={selectedOption} disabled={disabled} onChange={event => onChange({ data: event.target.value })}>
  {rgOptions.map(option => <option key={option.data} value={option.data}>{option.label}</option>)}
</select></label>;

export function ModalRoot({ children, onCancel }: Common & { onCancel?: () => void; closeModal?: () => void }) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>(".modal-card");
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>("button:not(:disabled), input, select") ?? []);
    focusable()[0]?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onCancel?.(); }
      if (event.key === "Tab") {
        const items = focusable();
        const index = items.indexOf(document.activeElement as HTMLElement);
        if (items.length && ((event.shiftKey && index <= 0) || (!event.shiftKey && index === items.length - 1))) {
          event.preventDefault(); (event.shiftKey ? items.at(-1) : items[0])?.focus();
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => { window.removeEventListener("keydown", handler); previous?.focus(); };
  }, [onCancel]);
  return <div className="modal-card" role="dialog" aria-modal="true" aria-label="LG TV Control">{children}</div>;
}
export function ConfirmModal({ strTitle, strDescription, strOKButtonText, onOK, onCancel }: {
  strTitle?: ReactNode; strDescription?: ReactNode; bAlertDialog?: boolean;
  strOKButtonText?: string; onOK?: () => void; onCancel?: () => void;
}) {
  return <ModalRoot onCancel={onCancel}><DialogHeader>{strTitle}</DialogHeader>
    <DialogBodyText>{strDescription}</DialogBodyText><DialogButton onClick={onOK}>{strOKButtonText ?? "OK"}</DialogButton></ModalRoot>;
}
export function showModal(content: ReactNode) {
  const element = document.createElement("div");
  element.className = "modal-backdrop";
  document.body.append(element);
  const root = createRoot(element);
  let closed = false;
  let resolve!: () => void;
  const ClosedPromise = new Promise<void>(done => { resolve = done; });
  root.render(content);
  return { ClosedPromise, Close() {
    if (closed) return;
    closed = true;
    root.unmount(); element.remove(); resolve();
  } };
}
