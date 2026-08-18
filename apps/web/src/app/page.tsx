import { ArchitectureFlow } from "@/components/architecture-flow";
import { FeatureGrid } from "@/components/feature-grid";
import { Hero } from "@/components/hero";
import { VersionRoadmap } from "@/components/version-roadmap";

export default function Home() {
  return (
    <div className="site-grid min-h-screen overflow-hidden">
      <header className="absolute inset-x-0 top-0 z-20">
        <div className="mx-auto flex h-20 w-full max-w-7xl items-center justify-between px-6 sm:px-10 lg:px-16">
          <a href="#top" className="flex items-center gap-3 font-mono text-xs text-zinc-200">
            <span className="grid size-7 place-items-center rounded border border-emerald-300/30 bg-emerald-300/10 text-emerald-300">
              I
            </span>
            sage
          </a>
          <nav className="flex items-center gap-5 font-mono text-[10px] uppercase tracking-[0.16em] text-zinc-500" aria-label="Primary navigation">
            <a className="transition hover:text-zinc-200" href="#architecture">Architecture</a>
            <a className="transition hover:text-zinc-200" href="#roadmap">Roadmap</a>
          </nav>
        </div>
      </header>

      <main id="top">
        <Hero />
        <ArchitectureFlow />
        <FeatureGrid />
        <VersionRoadmap />
      </main>

      <footer className="mx-auto w-full max-w-7xl px-6 py-14 sm:px-10 lg:px-16">
        <div className="flex flex-col justify-between gap-8 border-t border-white/[0.08] pt-8 md:flex-row md:items-end">
          <div>
            <p className="font-mono text-xs text-zinc-300">sage / V0.1</p>
            <p className="mt-3 max-w-xl text-xs leading-5 text-zinc-600">
              A project-owned LangGraph runtime controls reasoning and tool
              routing while repository execution remains isolated and
              deterministic.
            </p>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-600">
            <span>Python</span>
            <span>LangGraph</span>
            <span>Docker</span>
            <span>Next.js</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
