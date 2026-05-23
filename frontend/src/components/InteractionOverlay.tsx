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

  const type = String(node.node_type ?? node.type ?? "");

  if (type === "example_practice") {
    return (
      <div className="interaction-overlay">
        <div className="practice-card">
          <p>{node.content?.question || "练习"}</p>
          <div>
            {(node.content?.options || []).map((opt: string, i: number) => (
              <md-outlined-button key={i} onClick={onContinue}>
                {opt}
              </md-outlined-button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  const wordBank: string[] = node.content?.word_bank || [];

  return (
    <div className="interaction-overlay">
      <div className="sentence-card">
        <p>{node.content?.question || "连词成句"}</p>
        <div className="sentence-box">{currentSentence.join(" ")}</div>
        <div className="word-bank">
          {wordBank.map((word, i) => (
            <md-suggestion-chip key={i} label={word} onClick={() => onWordClick(word)} />
          ))}
        </div>
        <div>
          <md-text-button onClick={onReset}>重置</md-text-button>
          <md-filled-button onClick={onContinue}>确认</md-filled-button>
        </div>
      </div>
    </div>
  );
}
