"use client";

import { motion, useReducedMotion } from "motion/react";

const versions = [
  {
    version: "V0",
    title: "Local issue solver",
    detail: "A single agent produces a candidate diff in an isolated local clone.",
    status: "building now",
    current: true,
  },
  {
    version: "V1",
    title: "GitHub Actions integration",
    detail: "Authorized issue comments trigger the same controller and open draft PRs.",
    status: "next",
    current: false,
  },
  {
    version: "V2",
    title: "Multi-agent workflow",
    detail: "Project-owned orchestration separates exploration, implementation, and review.",
    status: "later",
    current: false,
  },
];

export function VersionRoadmap() {
  const reduceMotion = useReducedMotion();

  return (
    <section id="roadmap" className="border-y border-white/[0.08] bg-white/[0.015]">
      <div className="mx-auto w-full max-w-7xl px-6 py-24 sm:px-10 lg:px-16 lg:py-32">
        <div className="grid gap-12 lg:grid-cols-[0.65fr_1.35fr]">
          <div>
            <p className="section-kicker">Version roadmap</p>
            <h2 className="mt-4 text-4xl font-medium tracking-[-0.04em] text-white sm:text-5xl">
              Build the boundary once.
            </h2>
            <p className="mt-6 max-w-md text-sm leading-6 text-zinc-500">
              GitHub integration and multi-agent reasoning arrive in later
              versions without replacing repository execution or isolation.
            </p>
          </div>

          <div className="relative">
            <div className="absolute bottom-0 left-[19px] top-0 w-px bg-white/10" aria-hidden="true" />
            <motion.div
              aria-hidden="true"
              initial={{ scaleY: 0 }}
              whileInView={{ scaleY: 1 }}
              viewport={{ once: true, amount: 0.3 }}
              transition={{ duration: reduceMotion ? 0 : 0.9 }}
              className="absolute left-[19px] top-0 h-1/3 w-px origin-top bg-emerald-300"
            />
            <div className="space-y-3">
              {versions.map((item, index) => (
                <motion.article
                  key={item.version}
                  initial={{ opacity: 0, x: reduceMotion ? 0 : 12 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, amount: 0.5 }}
                  transition={{ duration: reduceMotion ? 0 : 0.4, delay: index * 0.1 }}
                  className="relative grid grid-cols-[40px_1fr] gap-5"
                >
                  <div className="relative z-10 mt-7 flex size-10 items-center justify-center rounded-full border border-white/10 bg-[#0b0d0c] font-mono text-[10px] text-zinc-400">
                    {item.version}
                  </div>
                  <div className={`rounded-md border p-6 ${item.current ? "border-emerald-300/20 bg-emerald-300/[0.035]" : "border-white/[0.08] bg-black/10"}`}>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <h3 className={item.current ? "text-zinc-100" : "text-zinc-400"}>
                        {item.title}
                      </h3>
                      <span className={`font-mono text-[9px] uppercase tracking-[0.16em] ${item.current ? "text-emerald-300" : "text-zinc-600"}`}>
                        {item.status}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-zinc-600">
                      {item.detail}
                    </p>
                  </div>
                </motion.article>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
