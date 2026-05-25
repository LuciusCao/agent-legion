import type { InteractionNode } from "../types";

interface InteractionOverlayProps {
  node: InteractionNode | null;
  currentSentence: string[];
  onWordClick: (word: string) => void;
  onReset: () => void;
  onContinue: () => void;
}

export function InteractionOverlay({
  node,
  currentSentence,
  onWordClick,
  onReset,
  onContinue,
}: InteractionOverlayProps) {
  if (!node) return null;

  const type = String(node.type ?? "");

  if (type === "example_practice") {
    return (
      <div className="interaction-overlay">
        <div className="practice-card">
          <p>{node.instruction || "练习"}</p>
          {node.hint && (
            <p style={{ fontSize: "0.875rem", color: "var(--md-sys-color-outline)" }}>
              提示：{node.hint}
            </p>
          )}
          {node.options && node.options.length > 0 && (
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
              {node.options.map((opt, i) => (
                <md-outlined-button key={i} onClick={onContinue}>
                  {opt.text}
                </md-outlined-button>
              ))}
            </div>
          )}
          <div style={{ marginTop: "12px" }}>
            <md-filled-button onClick={onContinue}>继续</md-filled-button>
          </div>
        </div>
      </div>
    );
  }

  if (node.options && node.options.length > 0) {
    return (
      <div className="interaction-overlay">
        <div className="practice-card">
          <p>{node.instruction || "互动"}</p>
          {node.reference_sentence && (
            <p style={{ fontSize: "0.875rem", color: "var(--md-sys-color-outline)" }}>
              {node.reference_sentence}
            </p>
          )}
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
            {node.options.map((opt, i) => (
              <md-outlined-button key={i} onClick={onContinue}>
                {opt.text}
              </md-outlined-button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // Fallback: sentence-building or generic interaction
  const words = node.answer || [];
  return (
    <div className="interaction-overlay">
      <div className="sentence-card">
        <p>{node.instruction || "连词成句"}</p>
        <div className="sentence-box">{currentSentence.join(" ")}</div>
        {words.length > 0 && (
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {words.map((word, index) => (
              <md-outlined-button key={`${word}-${index}`} onClick={() => onWordClick(word)}>
                {word}
              </md-outlined-button>
            ))}
          </div>
        )}
        <div>
          <md-text-button onClick={onReset}>重置</md-text-button>
          <md-filled-button onClick={onContinue}>确认</md-filled-button>
        </div>
      </div>
    </div>
  );
}
