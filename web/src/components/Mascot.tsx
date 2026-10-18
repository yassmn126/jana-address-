import alert from "../assets/mascot/alert.svg";
import celebrating from "../assets/mascot/celebrating.svg";
import hello from "../assets/mascot/hello.svg";
import laptop from "../assets/mascot/laptop.svg";
import magnifier from "../assets/mascot/magnifier.svg";
import oops from "../assets/mascot/oops.svg";
import report from "../assets/mascot/report.svg";
import sleeping from "../assets/mascot/sleeping.svg";
import standing from "../assets/mascot/standing.svg";
import thinking from "../assets/mascot/thinking.svg";

/** Poses of the brand mascot, exported from the Figma «Mascot» page (component set 3:24). */
export type Pose =
  | "hello" | "standing" | "thinking" | "sleeping" | "celebrating"
  | "oops" | "magnifier" | "report" | "alert" | "laptop";

const POSE_SRC: Record<Pose, string> = {
  hello, standing, thinking, sleeping, celebrating, oops, magnifier, report, alert, laptop,
};

/** App state → pose. `confused` means «needs a human look», `error` means something failed. */
export type Mood = "idle" | "waiting" | "sleeping" | "happy" | "confused" | "error";

export const MOOD_POSE: Record<Mood, Pose> = {
  idle: "standing",
  waiting: "thinking",
  sleeping: "sleeping",
  happy: "celebrating",
  confused: "magnifier",
  error: "oops",
};

export const MOOD_LABEL: Record<Mood, string> = {
  idle: "Ассистент",
  waiting: "Работаю",
  sleeping: "API недоступен",
  happy: "Готово",
  confused: "Нужна проверка",
  error: "Ошибка",
};

/** What the mascot currently says: a mood, a sentence and optionally one action. */
export interface MascotState {
  mood: Mood;
  text: string;
  action?: { label: string; run: () => void };
}

export function Mascot({ mood = "idle", pose, size, className = "" }: { mood?: Mood; pose?: Pose; size?: number; className?: string }) {
  const p = pose ?? MOOD_POSE[mood];
  return (
    <img
      className={`mascot ${className}`}
      src={POSE_SRC[p]}
      data-pose={p}
      alt=""
      aria-hidden="true"
      draggable={false}
      decoding="async"
      style={size ? { width: size, height: size } : undefined}
    />
  );
}

/** Compact assistant row: small mascot, label and the current sentence. */
export function MascotAssistant({ state }: { state: MascotState }) {
  return (
    <div className="assistant pop" key={state.mood + state.text} data-mood={state.mood}>
      <Mascot mood={state.mood} />
      <div>
        <p className="eyebrow">{MOOD_LABEL[state.mood]}</p>
        <p>{state.text}</p>
        {state.action && <button type="button" className="link-btn" onClick={state.action.run}>{state.action.label}</button>}
      </div>
    </div>
  );
}
