export default function PanelToggle({ label, target, collapsed, onToggle }: {
  label: string;
  target: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" aria-expanded={!collapsed} aria-controls={target}
      aria-label={`${collapsed ? "Expand" : "Minimize"} ${label}`} onClick={onToggle}
      className="rounded border border-line px-2 py-1 text-xs text-ink-2 hover:bg-panel-2">
      {collapsed ? "Expand" : "Minimize"}
    </button>
  );
}
