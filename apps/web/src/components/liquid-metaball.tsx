"use client";

import { useEffect, useRef } from "react";

type Orb = {
  angle: number;
  distance: number;
  radius: number;
  speed: number;
  phase: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
};

const ORB_CONFIG = [
  [0.2, 32, 122, 0.26],
  [1.4, 88, 76, -0.34],
  [2.5, 94, 84, 0.3],
  [3.7, 84, 68, -0.38],
  [4.8, 90, 72, 0.33],
  [5.7, 86, 78, -0.28],
  [0.9, 120, 48, 0.44],
] as const;

const DROPLET_SPEED = 0.34;
const ORBIT_SPEED = 0.048;
const BREATHING_SPEED = 5;
const RADIUS_SPEED = 6.4;
const DISPERSION_DURATION_MS = 425;
const DISPERSION_FORCE = 14;
const DISPERSION_FORCE_STEP = 3.6;
const DISPERSION_DRAG = 0.93;
const RECOVERY_VELOCITY_DRAG = 0.7;

function makeOrbs(centerX: number, centerY: number): Orb[] {
  return ORB_CONFIG.map(([angle, distance, radius, speed], index) => ({
    angle,
    distance,
    radius,
    speed,
    phase: index * 0.83,
    x: centerX + Math.cos(angle) * distance,
    y: centerY + Math.sin(angle) * distance,
    vx: 0,
    vy: 0,
  }));
}

export function LiquidMetaball() {
  const svgRef = useRef<SVGSVGElement>(null);
  const circleRefs = useRef<Array<SVGCircleElement | null>>([]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    let frame = 0;
    let width = svg.clientWidth;
    let height = svg.clientHeight;
    let centerX = width / 2;
    let centerY = height / 2;
    let targetX = centerX;
    let targetY = centerY;
    let dispersedUntil = 0;
    const orbs = makeOrbs(centerX, centerY);
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resize = () => {
      const previousWidth = width;
      const previousHeight = height;
      width = svg.clientWidth;
      height = svg.clientHeight;
      const dx = (width - previousWidth) / 2;
      const dy = (height - previousHeight) / 2;
      centerX += dx;
      centerY += dy;
      targetX += dx;
      targetY += dy;
      orbs.forEach((orb) => {
        orb.x += dx;
        orb.y += dy;
      });
    };

    const getPoint = (event: PointerEvent) => {
      const rect = svg.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const move = (event: PointerEvent) => {
      if (prefersReducedMotion || event.pointerType === "touch") return;
      const point = getPoint(event);
      targetX = Math.max(width * 0.22, Math.min(width * 0.78, point.x));
      targetY = Math.max(height * 0.28, Math.min(height * 0.72, point.y));
    };

    const leave = () => {
      targetX = width / 2;
      targetY = height / 2;
    };

    const disperse = (event: PointerEvent) => {
      if (prefersReducedMotion) return;
      const point = getPoint(event);
      const distanceFromBlob = Math.hypot(point.x - centerX, point.y - centerY);
      if (distanceFromBlob > Math.min(240, width * 0.28)) return;

      dispersedUntil = performance.now() + DISPERSION_DURATION_MS;
      orbs.forEach((orb, index) => {
        const angle = Math.atan2(orb.y - point.y, orb.x - point.x) + index * 0.08;
        const force = DISPERSION_FORCE + (index % 3) * DISPERSION_FORCE_STEP;
        orb.vx = Math.cos(angle) * force;
        orb.vy = Math.sin(angle) * force;
      });
    };

    const animate = (time: number) => {
      centerX += (targetX - centerX) * DROPLET_SPEED;
      centerY += (targetY - centerY) * DROPLET_SPEED;
      const isDispersed = time < dispersedUntil;
      const elapsed = time / 1000;

      orbs.forEach((orb, index) => {
        orb.angle += orb.speed * ORBIT_SPEED;
        const breathing = Math.sin(elapsed * BREATHING_SPEED + orb.phase) * 14;
        const orbitDistance = orb.distance + breathing;
        const restingX = centerX + Math.cos(orb.angle) * orbitDistance;
        const restingY = centerY + Math.sin(orb.angle) * orbitDistance * 0.62;

        if (isDispersed) {
          orb.x += orb.vx;
          orb.y += orb.vy;
          orb.vx *= DISPERSION_DRAG;
          orb.vy *= DISPERSION_DRAG;
        } else {
          orb.x += (restingX - orb.x) * DROPLET_SPEED;
          orb.y += (restingY - orb.y) * DROPLET_SPEED;
          orb.vx *= RECOVERY_VELOCITY_DRAG;
          orb.vy *= RECOVERY_VELOCITY_DRAG;
        }

        const circle = circleRefs.current[index];
        if (circle) {
          circle.setAttribute("cx", orb.x.toFixed(2));
          circle.setAttribute("cy", orb.y.toFixed(2));
          circle.setAttribute(
            "r",
            (orb.radius + Math.sin(elapsed * RADIUS_SPEED + orb.phase) * 7).toFixed(2),
          );
        }
      });

      frame = requestAnimationFrame(animate);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(svg);
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
    svg.addEventListener("pointerdown", disperse);

    if (prefersReducedMotion) {
      orbs.forEach((orb, index) => {
        const circle = circleRefs.current[index];
        circle?.setAttribute("cx", orb.x.toFixed(2));
        circle?.setAttribute("cy", orb.y.toFixed(2));
        circle?.setAttribute("r", orb.radius.toFixed(2));
      });
    } else {
      frame = requestAnimationFrame(animate);
    }

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerleave", leave);
      svg.removeEventListener("pointerdown", disperse);
    };
  }, []);

  return (
    <svg
      ref={svgRef}
      className="liquid-layer"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <filter id="liquid-goo" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="18" result="blur" />
          <feColorMatrix
            in="blur"
            mode="matrix"
            values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 30 -13"
            result="goo"
          />
        </filter>
      </defs>
      <g filter="url(#liquid-goo)" fill="#f4f4f1">
        {ORB_CONFIG.map((_, index) => (
          <circle
            key={index}
            ref={(node) => {
              circleRefs.current[index] = node;
            }}
          />
        ))}
      </g>
    </svg>
  );
}
