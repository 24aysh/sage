"use client";

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger, useGSAP);

export function ScrollMotion() {
  useGSAP(() => {
    const mediaQuery = gsap.matchMedia();

    mediaQuery.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.utils.toArray<HTMLElement>("[data-media-panel]").forEach((panel) => {
        gsap.fromTo(
          panel,
          { autoAlpha: 0.25, scale: 0.8 },
          {
            autoAlpha: 1,
            scale: 1,
            ease: "none",
            scrollTrigger: {
              trigger: panel,
              start: "top 92%",
              end: "center 58%",
              scrub: 0.7,
            },
          },
        );

        gsap.to(panel, {
          autoAlpha: 0.25,
          ease: "none",
          scrollTrigger: {
            trigger: panel,
            start: "bottom 22%",
            end: "bottom top",
            scrub: 0.7,
          },
        });
      });
    });

    return () => mediaQuery.revert();
  }, []);

  return null;
}
