"use client";

import { motion, useReducedMotion } from "motion/react";

const stages = [
  { index: "01", name: "Issue", detail: "written task", current: true },
  { index: "02", name: "Agent", detail: "engineering judgment", current: true },
  { index: "03", name: "Repository", detail: "isolated clone", current: true },
  { index: "04", name: "Patch", detail: "verified diff", current: true },
  { index: "05", name: "Pull request", detail: "V1 publishing", current: false },
];

export function ArchitectureFlow() {
  const reduceMotion = useReducedMotion();

  return (
    <section
      id="architecture"
      className="scroll-mt-16 border-y border-white/[0.08] bg-[#090b0a]/80"
    >
      <div className="mx-auto w-full max-w-7xl px-6 py-24 sm:px-10 lg:px-16 lg:py-32">
        <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
          <div>
            <p className="section-kicker">Execution path</p>
            <h2 className="mt-4 max-w-2xl text-4xl font-medium tracking-[-0.04em] text-white sm:text-5xl">
              Judgment at the top. Ground truth underneath.
            </h2>
          </div>
          <p className="max-w-md text-sm leading-6 text-zinc-500">
            V0 ends at a local candidate patch. Pull-request publishing is shown
            as future context—not as a capability that exists today.
          </p>
        </div>

        <div className="mt-16 grid gap-3 lg:grid-cols-[1fr_32px_1fr_32px_1fr_32px_1fr_32px_1fr] lg:items-stretch">
          {stages.map((stage, index) => (
            <div key={stage.name} className="contents">
              <motion.article
                initial={{ opacity: 0, y: reduceMotion ? 0 : 14 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.5 }}
                transition={{ duration: reduceMotion ? 0 : 0.45, delay: index * 0.08 }}
                className={`relative min-h-40 rounded-md border p-5 ${
                  stage.current
                    ? "border-white/10 bg-white/[0.025]"
                    : "border-dashed border-zinc-700/80 bg-transparent"
                }`}
              >
                <div className="flex items-start justify-between gap-4">
                  <span className="font-mono text-[11px] text-zinc-600">
                    {stage.index}
                  </span>
                  <span
                    className={`rounded-full px-2 py-1 font-mono text-[9px] uppercase tracking-[0.16em] ${
                      stage.current
                        ? "bg-emerald-400/10 text-emerald-300"
                        : "bg-white/5 text-zinc-600"
                    }`}
                  >
                    {stage.current ? "V0" : "V1"}
                  </span>
                </div>
                <h3 className={`mt-10 text-lg ${stage.current ? "text-zinc-100" : "text-zinc-500"}`}>
                  {stage.name}
                </h3>
                <p className="mt-1 font-mono text-[11px] text-zinc-600">
                  {stage.detail}
                </p>
              </motion.article>

              {index < stages.length - 1 ? (
                <div className="relative hidden items-center lg:flex" aria-hidden="true">
                  <div className="h-px w-full overflow-hidden bg-white/10">
                    <motion.div
                      initial={{ x: "-100%" }}
                      whileInView={{ x: "100%" }}
                      viewport={{ once: false }}
                      transition={{
                        duration: reduceMotion ? 0 : 1.6,
                        delay: index * 0.12,
                        repeat: reduceMotion ? 0 : Infinity,
                        repeatDelay: 1.2,
                      }}
                      className="h-full w-1/2 bg-gradient-to-r from-transparent via-emerald-300 to-transparent"
                    />
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>

        <div className="mt-6 flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-600">
          <span className="h-px flex-1 bg-white/[0.07]" />
          <span>Current boundary: patch persisted for human review</span>
        </div>
      </div>
    </section>
  );
}
