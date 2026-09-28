import type { ProgressiveSearchSnapshot } from "../lib/types";
import AutonomousResults from "./AutonomousSearchResults";
import LegacyResults from "./search-history/LegacySearchResults";

export default function ProgressiveSearchResults({ data }: { data: ProgressiveSearchSnapshot }) {
  return data.mode === "autonomous" ? <AutonomousResults data={data} /> : <LegacyResults data={data} />;
}
