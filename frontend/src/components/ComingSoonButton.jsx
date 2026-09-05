import { useId } from "react";

export function ComingSoonButton({ active = false, children, className = "" }) {
  const tooltipId = useId();

  return (
    <span className={`coming-soon ${className}`.trim()}>
      <button
        aria-describedby={tooltipId}
        className={active ? "active" : ""}
        type="button"
      >
        {children}
      </button>
      <span className="coming-soon-tooltip" id={tooltipId} role="tooltip">
        Coming soon
      </span>
    </span>
  );
}
