import { useId } from "react";

/** The deck's green ghost. CSS in styles.css reads data-mood to pick the face and motion. */
export type Mood = "idle" | "waiting" | "sleeping" | "happy" | "confused";

export const MOOD_LABEL: Record<Mood, string> = {
  idle: "ассистент",
  waiting: "думаю",
  sleeping: "сплю",
  happy: "готово",
  confused: "нужна проверка",
};

/** What the mascot currently shows: a mood, a sentence and optionally one action. */
export interface MascotState {
  mood: Mood;
  text: string;
  action?: { label: string; run: () => void };
}

const BODY =
  "M100 20C147 20 172 58 172 106C172 136 170 158 164 174C160 188 146 192 136 184C128 194 112 196 100 188C88 196 72 194 64 184C54 192 40 188 36 174C30 158 28 136 28 106C28 58 53 20 100 20Z";

const star = (cx: number, cy: number, r: number) => {
  const k = r * 0.28;
  return `M${cx} ${cy - r}L${cx + k} ${cy - k}L${cx + r} ${cy}L${cx + k} ${cy + k}L${cx} ${cy + r}L${cx - k} ${cy + k}L${cx - r} ${cy}L${cx - k} ${cy - k}Z`;
};

export function Mascot({ mood, className = "" }: { mood: Mood; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={`mascot ${className}`} data-mood={mood} viewBox="0 0 200 212" role="img" aria-label={`Талисман Jana Address: ${MOOD_LABEL[mood]}`}>
      <defs>
        <radialGradient id={`${id}b`} cx="36%" cy="24%" r="85%">
          <stop offset="0" stopColor="#f6fbe0" />
          <stop offset=".22" stopColor="#dcf184" />
          <stop offset=".5" stopColor="#bce33c" />
          <stop offset=".86" stopColor="#86ad1c" />
          <stop offset="1" stopColor="#5f8314" />
        </radialGradient>
        <radialGradient id={`${id}s`} cx="50%" cy="100%" r="58%">
          <stop offset="0" stopColor="#3f5a0e" stopOpacity=".6" />
          <stop offset="1" stopColor="#3f5a0e" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse className="m-ground" cx="100" cy="202" rx="52" ry="7" />
      <g className="m-body">
        <ellipse className="m-arm m-arm-l" cx="31" cy="128" rx="11" ry="15" fill={`url(#${id}b)`} style={{ transformOrigin: "85% 18%" }} />
        <ellipse className="m-arm m-arm-r" cx="169" cy="128" rx="11" ry="15" fill={`url(#${id}b)`} style={{ transformOrigin: "15% 18%" }} />
        <path d={BODY} fill={`url(#${id}b)`} />
        <path d={BODY} fill={`url(#${id}s)`} />
        <circle className="m-spot" cx="56" cy="152" r="9" />
        <circle className="m-spot" cx="146" cy="144" r="7" />
        <circle className="m-spot" cx="154" cy="162" r="4" />
        <circle className="m-spot" cx="47" cy="126" r="4" />
        <circle className="m-spot" cx="132" cy="168" r="5" />
        <ellipse className="m-shine" cx="70" cy="50" rx="22" ry="11" transform="rotate(-30 70 50)" />
        <circle className="m-shine" cx="50" cy="76" r="4" opacity=".5" />
        <ellipse className="m-cheek" cx="58" cy="120" rx="9" ry="5.5" />
        <ellipse className="m-cheek" cx="142" cy="120" rx="9" ry="5.5" />
        <g className="m-eyes m-eyes-open">
          <ellipse cx="78" cy="90" rx="18" ry="21" fill="#fff" />
          <ellipse cx="122" cy="90" rx="18" ry="21" fill="#fff" />
          <g className="m-pupil m-pupil-l"><circle className="m-dark" cx="82" cy="86" r="9.5" /><circle cx="85.5" cy="82" r="3.2" fill="#fff" /></g>
          <g className="m-pupil m-pupil-r"><circle className="m-dark" cx="126" cy="86" r="9.5" /><circle cx="129.5" cy="82" r="3.2" fill="#fff" /></g>
        </g>
        <g className="m-eyes m-eyes-closed"><path className="m-ink" d="M62 94Q78 104 94 94" /><path className="m-ink" d="M106 94Q122 104 138 94" /></g>
        <g className="m-eyes m-eyes-happy"><path className="m-ink" d="M62 96Q78 78 94 96" /><path className="m-ink" d="M106 96Q122 78 138 96" /></g>
        <path className="m-brow m-ink" d="M106 60Q122 50 140 58" />
        <g className="m-mouth m-mouth-smile"><path className="m-ink" d="M88 124Q100 136 112 124" /></g>
        <g className="m-mouth m-mouth-open"><path className="m-dark" d="M84 120Q100 150 116 120Z" /><ellipse className="m-tongue" cx="100" cy="135" rx="7" ry="4.5" /></g>
        <g className="m-mouth m-mouth-wavy"><path className="m-ink" d="M84 130Q89 124 94 130T104 130T114 130" /></g>
        <g className="m-mouth m-mouth-o"><ellipse className="m-dark" cx="100" cy="128" rx="6" ry="7" /></g>
        <g className="m-mouth m-mouth-sleep"><path className="m-ink" d="M93 128Q100 132 107 128" /></g>
      </g>
      <g className="m-extra m-zzz">
        <text className="m-z" x="150" y="44" fontSize="20">z</text>
        <text className="m-z" x="163" y="30" fontSize="16">z</text>
        <text className="m-z" x="175" y="18" fontSize="13">z</text>
      </g>
      <g className="m-extra m-sparkles">
        <path className="m-sparkle" d={star(24, 40, 9)} />
        <path className="m-sparkle" d={star(178, 50, 7)} />
        <path className="m-sparkle" d={star(12, 122, 6)} />
        <path className="m-sparkle" d={star(188, 116, 8)} />
      </g>
      <g className="m-extra m-question"><text x="158" y="50" fontSize="38" style={{ fill: "#f59a8f" }}>?</text></g>
      <g className="m-extra m-dots">
        <rect className="m-bubble" x="140" y="14" width="54" height="26" rx="13" />
        <circle className="m-bubble-dot" cx="154" cy="27" r="3.5" />
        <circle className="m-bubble-dot" cx="167" cy="27" r="3.5" />
        <circle className="m-bubble-dot" cx="180" cy="27" r="3.5" />
      </g>
    </svg>
  );
}

const eyebrowTone = (m: Mood) => (m === "happy" ? "" : m === "confused" ? "flag" : "muted");

/** Dashboard hero: speech bubble + large mascot. */
export function MascotBubble({ state }: { state: MascotState }) {
  return (
    <div className="hero-mascot" data-mood={state.mood}>
      <div className="bubble pop" key={state.mood + state.text} role="status" aria-live="polite">
        <p className={`eyebrow ${eyebrowTone(state.mood)}`}>{MOOD_LABEL[state.mood]}</p>
        <p>{state.text}</p>
        {state.action && <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 12 }} onClick={state.action.run}>{state.action.label}</button>}
      </div>
      <Mascot mood={state.mood} />
    </div>
  );
}

/** Page headers: compact assistant row mirroring the same state. */
export function MascotAssistant({ state }: { state: MascotState }) {
  return (
    <div className="assistant pop" key={state.mood + state.text} data-mood={state.mood}>
      <Mascot mood={state.mood} />
      <div>
        <p className={`eyebrow ${eyebrowTone(state.mood)}`}>{MOOD_LABEL[state.mood]}</p>
        <p>{state.text}</p>
        {state.action && <button type="button" className="link-btn" onClick={state.action.run}>{state.action.label}</button>}
      </div>
    </div>
  );
}
