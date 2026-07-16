import type { Rating } from "../types";
import { ratingRole, roleClasses } from "../lib/theme";

export default function RatingBadge({ rating }: { rating: Rating }) {
  const cls = roleClasses[ratingRole(rating)];
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-3 py-1 text-xs font-semibold tracking-wide ${cls.bg} ${cls.text} ${cls.border}`}
    >
      {rating.toUpperCase()}
    </span>
  );
}
