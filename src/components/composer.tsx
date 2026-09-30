"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { useControl } from "./provider";
import { EXAMPLE_OBJECTIVE } from "@/lib/demo/fixtures";

const examples = [
  { label: "Prepare a launch brief", value: EXAMPLE_OBJECTIVE },
  {
    label: "Analyze this dataset",
    value:
      "Analyze the provided public sample dataset. Identify patterns and propose a clear data visualization. Do not publish or contact anyone.",
  },
  {
    label: "Plan a research project",
    value:
      "Plan a research project for our student-built product. Outline questions, public sources, and an execution checklist. Do not contact anyone.",
  },
];
export function MissionComposer({
  compact = false,
  onSubmitted,
  initialObjective = "",
  inputId,
  onObjectiveChange,
}: {
  compact?: boolean;
  onSubmitted?: () => void;
  initialObjective?: string;
  inputId?: string;
  onObjectiveChange?: (value: string) => void;
}) {
  const { api } = useControl();
  const router = useRouter();
  const [objective, setObjective] = useState(initialObjective);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const key = useRef<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (lock.current) return;
    if (!objective.trim()) {
      setError("Describe an objective to begin.");
      input.current?.focus();
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    key.current ??= crypto.randomUUID();
    try {
      const mission = await api.createMission({
        objective,
        idempotencyKey: key.current,
      });
      onObjectiveChange?.("");
      onSubmitted?.();
      router.push(`/missions/${mission.id}`);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create this mission. Try again.",
      );
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <form
      className={`composer ${compact ? "compact-composer" : ""}`}
      onSubmit={submit}
    >
      <label
        htmlFor={inputId ?? (compact ? "dialog-objective" : "objective")}
        className="composer-label"
      >
        Your objective
      </label>
      <div className={`composer-input ${error ? "invalid" : ""}`}>
        <textarea
          ref={input}
          id={inputId ?? (compact ? "dialog-objective" : "objective")}
          value={objective}
          autoFocus
          maxLength={2000}
          rows={3}
          onChange={(e) => {
            setObjective(e.target.value);
            onObjectiveChange?.(e.target.value);
            key.current = null;
            setError("");
          }}
          placeholder="What would you like to accomplish?"
          aria-describedby={error ? "composer-error" : "composer-hint"}
          aria-invalid={!!error}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey))
              e.currentTarget.form?.requestSubmit();
          }}
        />
        <button
          type="submit"
          className="button primary run-button"
          disabled={busy}
          aria-label={busy ? "Opening mission" : "Start mission"}
        >
          {busy ? "Opening…" : "Start mission"}
          <ArrowRight size={20} aria-hidden="true" />
        </button>
      </div>
      {error && (
        <p className="form-error" id="composer-error" role="alert">
          {error}
        </p>
      )}
      <div className="composer-meta" id="composer-hint">
        <span>Control / Command + Enter to submit. Public sample data only.</span>
      </div>
      <details className="composer-examples">
        <summary>Try an example</summary>
        <div className="examples">
        {examples.map((example) => (
          <button
            type="button"
            key={example.label}
            onClick={() => {
              setObjective(example.value);
              onObjectiveChange?.(example.value);
              setError("");
              key.current = null;
              input.current?.focus();
            }}
          >
            {example.label}
          </button>
        ))}
        </div>
      </details>
    </form>
  );
}
