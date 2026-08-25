const features = [
  {
    title: "Reads between the lines",
    visual: (
      <div className="feature-accordion" aria-hidden="true">
        <span><i>Issue</i><b>Ask</b></span>
        <span><i>Repository</i><b>Context</b></span>
        <span><i>Change</i><b>Intent</b></span>
      </div>
    ),
  },
  {
    title: "Works where the rules live",
    visual: (
      <div className="orbit-visual" aria-hidden="true">
        <span className="orbit-ring orbit-ring-one" />
        <span className="orbit-ring orbit-ring-two" />
        <span className="orbit-core">S</span>
      </div>
    ),
  },
  {
    title: "Stops before certainty becomes theatre",
    visual: (
      <div className="handoff-visual" aria-hidden="true">
        <span>issue</span>
        <i />
        <span>worktree</span>
        <i />
        <span>draft</span>
      </div>
    ),
  },
  {
    title: "Leaves a reviewable trail",
    visual: (
      <div className="diff-visual" aria-hidden="true">
        <span className="diff-line removed">− stale path</span>
        <span className="diff-line added">+ intended behavior</span>
        <span className="diff-line added">+ focused coverage</span>
      </div>
    ),
  },
  {
    title: "You keep the merge button",
    visual: (
      <div className="control-visual" aria-hidden="true">
        <span className="control-track">
          <span className="control-thumb" />
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/45">
          Review required
        </span>
      </div>
    ),
  },
];

export function FeatureGrid() {
  return (
    <section id="capabilities" className="pb-16 pt-2 md:pt-3">
      <div className="page-shell">
        <div className="feature-bento">
          {features.map((feature, index) => (
            <article
              key={feature.title}
              className={`feature-card feature-card-${index + 1}`}
            >
              <h3>{feature.title}</h3>
              <div className="feature-media" data-media-panel>
                {feature.visual}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
