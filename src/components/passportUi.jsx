const ICON_PATH = "/assets/licensed/icon-";

export function Icon({ name, size = 18, className = "" }) {
  return (
    <span
      className={`library-icon ${className}`.trim()}
      style={{ "--icon-source": `url(${ICON_PATH}${name}.svg)`, "--icon-size": `${size}px` }}
      aria-hidden="true"
    />
  );
}

export function StatusStamp({ children, tone = "blue", compact = false }) {
  return <span className={`status-stamp status-${tone}${compact ? " compact" : ""}`}>{children}</span>;
}

export function PageHeading({ eyebrow, title, note, page }) {
  return (
    <header className="page-heading">
      <img className="page-brand-mark" src="/assets/brand/curate-favicon.png" alt="" />
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        {note ? <p className="page-note">{note}</p> : null}
      </div>
      <span className="page-number">PAGE {page}</span>
    </header>
  );
}

export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`field ${className}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function ActionButton({ children, variant = "primary", busy = false, disabled = false, ...props }) {
  return (
    <button {...props} className={`action-button action-${variant}`} disabled={busy || disabled}>
      {busy ? <span className="button-working"><i aria-hidden="true" />CURATING</span> : children}
    </button>
  );
}

export function ReceiptCard({ receipt, selected, onSelect, compact = false }) {
  return (
    <button
      type="button"
      className={`receipt-card${selected ? " selected" : ""}${compact ? " receipt-compact" : ""}`}
      onClick={() => onSelect?.(receipt)}
    >
      <span className="receipt-id">{receipt.id}</span>
      <strong>{receipt.type}</strong>
      {!compact ? <span>{receipt.detail}</span> : null}
      <small>{receipt.time}</small>
      <StatusStamp tone={receipt.status.includes("Succeeded") ? "green" : receipt.status.includes("Needs") ? "orange" : "blue"} compact>
        {receipt.status}
      </StatusStamp>
    </button>
  );
}

export function CompositionBar({ topics }) {
  return (
    <div className="composition-wrap" aria-label="Topic composition">
      <div className="composition-bar">
        {topics.map((topic) => (
          <span
            key={topic.id}
            className={`composition-segment segment-${topic.color}`}
            style={{ width: `${topic.percent}%` }}
            title={`${topic.label}: ${topic.percent}%`}
          />
        ))}
      </div>
      <div className="composition-key">
        {topics.map((topic) => (
          <span key={topic.id}>
            <i className={`key-swatch segment-${topic.color}`} />
            {topic.label}
            <b>{topic.percent}%</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export function PlatformVisa({ destination, connected, onToggle, featured = false }) {
  return (
    <article className={`platform-visa visa-${destination.tone}${featured ? " featured" : ""}`}>
      <div className="visa-copy">
        <p className="visa-country">DESTINATION VISA</p>
        <h3>{destination.name}</h3>
        <div className="visa-status-row">
          <StatusStamp tone={destination.tone}>{destination.status}</StatusStamp>
          <span>{destination.certification}</span>
        </div>
        <p className="visa-limitation">{destination.limitation}</p>
      </div>
      <div className="visa-actions">
        <b>Agent may</b>
        {destination.actions.slice(0, featured ? 4 : 3).map((action) => (
          <span key={action}>{action}</span>
        ))}
      </div>
      {onToggle ? (
        <button
          type="button"
          className={`visa-seal${connected ? " visa-seal-connected" : ""}`}
          onClick={() => onToggle(destination.id)}
          aria-pressed={connected}
        >
          <span>{connected ? "SELECTED" : "SELECT"}</span>
          <b>{destination.shortName.toUpperCase()}</b>
          <small>{connected ? "INCLUDED" : "CHOOSE"}</small>
        </button>
      ) : (
        <div className="visa-seal visa-seal-connected">
          <span>PROFILED</span>
          <b>{destination.shortName.toUpperCase()}</b>
          <small>CAPABILITY</small>
        </div>
      )}
    </article>
  );
}
