"use client";

import { motion, useReducedMotion } from "motion/react";

const reveal = {
  hidden: { opacity: 0, y: 18 },
  visible: { opacity: 1, y: 0 },
};

export function Hero() {
  const reduceMotion = useReducedMotion();
  const transition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.6, ease: [0.22, 1, 0.36, 1] as const };

  return (
    <section className="relative mx-auto flex min-h-[760px] w-full max-w-7xl flex-col justify-center px-6 pb-24 pt-32 sm:px-10 lg:px-16">
      <motion.div
        initial="hidden"
        animate="visible"
        transition={{ staggerChildren: reduceMotion ? 0 : 0.1 }}
        className="relative z-10 max-w-5xl"
      >
        <motion.div
          variants={reveal}
          transition={transition}
          className="mb-8 inline-flex items-center gap-3 rounded-full border border-white/10 bg-white/[0.035] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-400"
        >
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-50 motion-reduce:animate-none" />
            <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
          </span>
          V0 · local issue solver
        </motion.div>

        <motion.h1
          variants={reveal}
          transition={transition}
          className="max-w-5xl text-balance text-5xl font-medium leading-[0.98] tracking-[-0.055em] text-white sm:text-7xl lg:text-[92px]"
        >
          Turn GitHub issues into{" "}
          <span className="text-zinc-500">code changes.</span>
        </motion.h1>

        <motion.p
          variants={reveal}
          transition={transition}
          className="mt-8 max-w-2xl text-pretty text-lg leading-8 text-zinc-400 sm:text-xl"
        >
          An execution-grounded engineering agent that reasons about code while
          deterministic tools own every read, command, and edit inside an
          isolated repository workspace.
        </motion.p>

        <motion.div
          variants={reveal}
          transition={transition}
          className="mt-10 flex flex-col gap-3 sm:flex-row"
        >
          <a
            href="#architecture"
            className="inline-flex h-12 items-center justify-center gap-3 rounded-md bg-emerald-300 px-5 font-mono text-sm font-semibold text-emerald-950 transition hover:-translate-y-0.5 hover:bg-emerald-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-emerald-300"
          >
            View architecture
            <span aria-hidden="true">↓</span>
          </a>
          <a
            href="https://github.com/your-org/sage"
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-12 items-center justify-center gap-3 rounded-md border border-white/15 bg-white/[0.035] px-5 font-mono text-sm text-zinc-200 transition hover:-translate-y-0.5 hover:border-white/25 hover:bg-white/[0.06] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
          >
            GitHub
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">
              placeholder
            </span>
          </a>
        </motion.div>
      </motion.div>

      <motion.div
        aria-hidden="true"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: reduceMotion ? 0 : 1.2, delay: 0.3 }}
        className="absolute right-0 top-44 hidden w-[34%] border-y border-l border-white/[0.07] bg-black/30 p-5 font-mono text-[11px] leading-6 text-zinc-600 backdrop-blur-sm lg:block"
      >
        <div className="mb-3 flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-zinc-700" />
          <span className="size-2 rounded-full bg-zinc-700" />
          <span className="size-2 rounded-full bg-zinc-700" />
          <span className="ml-auto">run.log</span>
        </div>
        <p><span className="text-emerald-400/80">01</span> clone committed revision</p>
        <p><span className="text-emerald-400/80">02</span> start sealed workspace</p>
        <p><span className="text-emerald-400/80">03</span> inspect through bounded tools</p>
        <p><span className="text-emerald-400/80">04</span> verify actual git diff</p>
        <p className="mt-2 text-zinc-400">→ candidate patch persisted</p>
      </motion.div>
    </section>
  );
}
