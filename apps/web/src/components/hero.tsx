import { LiquidMetaball } from "@/components/liquid-metaball";

export function Hero() {
  return (
    <section id="top" className="page-shell pt-2 md:pt-3">
      <div className="hero-field">
        <div className="hero-grid" aria-hidden="true" />
        <LiquidMetaball />

        <div className="hero-content">
          <h1 className="hero-title w-full max-w-6xl">
            Ship the fix.
            <br />
            Skip the chase.
          </h1>
          <div className="mt-8 flex justify-center">
            <a className="primary-button" href="/docs">
              Try it for free
              <span aria-hidden="true">↗</span>
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
