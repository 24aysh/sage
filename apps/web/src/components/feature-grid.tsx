"use client";

import { motion, useReducedMotion } from "motion/react";

const features = [
  {
    code: "ISO",
    title: "Isolated execution",
    description:
      "Each run gets a committed clone in a disposable, network-disabled Docker workspace.",
  },
  {
    code: "RGT",
    title: "Repository-aware tools",
    description:
      "Bounded tree, search, read, patch, command, and diff operations keep every action explicit.",
  },
  {
    code: "AGT",
    title: "Agentic code reasoning",
    description:
      "The model chooses what to inspect and change; deterministic software performs the work.",
  },
  {
    code: "GIT",
    title: "GitHub-native roadmap",
    description:
      "The same controller and sandbox boundaries are designed to move into Actions in V1.",
  },
];

export function FeatureGrid() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="mx-auto w-full max-w-7xl px-6 py-24 sm:px-10 lg:px-16 lg:py-32">
      <p className="section-kicker">Engineering foundation</p>
      <div className="mt-10 grid border-l border-t border-white/[0.08] md:grid-cols-2">
        {features.map((feature, index) => (
          <motion.article
            key={feature.code}
            initial={{ opacity: 0, y: reduceMotion ? 0 : 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            whileHover={reduceMotion ? undefined : { y: -3 }}
            viewport={{ once: true, amount: 0.25 }}
            transition={{ duration: reduceMotion ? 0 : 0.45, delay: index * 0.06 }}
            className="group min-h-64 border-b border-r border-white/[0.08] bg-white/[0.012] p-7 transition-colors hover:bg-white/[0.025] sm:p-9"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-emerald-300/80">
                {feature.code}
              </span>
              <span className="font-mono text-xs text-zinc-700 transition-colors group-hover:text-zinc-500">
                0{index + 1}
              </span>
            </div>
            <h3 className="mt-20 text-xl font-medium text-zinc-100">
              {feature.title}
            </h3>
            <p className="mt-3 max-w-md text-sm leading-6 text-zinc-500">
              {feature.description}
            </p>
          </motion.article>
        ))}
      </div>
    </section>
  );
}
