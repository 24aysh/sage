import { FeatureGrid } from "@/components/feature-grid";
import { Hero } from "@/components/hero";
import { ScrollMotion } from "@/components/scroll-motion";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main className="w-full max-w-full overflow-x-hidden">
        <Hero />
        <FeatureGrid />
        <ScrollMotion />
      </main>
    </>
  );
}
