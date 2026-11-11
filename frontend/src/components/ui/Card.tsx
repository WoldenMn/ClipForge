import type { HTMLAttributes, ReactNode } from "react";

const PADDING = {
  none: "",
  sm: "p-3",
  md: "p-4",
  lg: "p-6",
} as const;

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** Reagit au survol : bordure plus nette, surface plus claire, 1 px de lift. */
  interactive?: boolean;
  padding?: keyof typeof PADDING;
  className?: string;
}

export function Card({
  children,
  interactive = false,
  padding = "md",
  className = "",
  ...rest
}: CardProps) {
  const hover = interactive
    ? "cursor-pointer transition duration-150 ease-soft hover:-translate-y-px hover:border-line-strong hover:bg-surface-hi hover:shadow-lift"
    : "";
  return (
    <div
      className={`rounded-card border border-line bg-surface shadow-card ${PADDING[padding]} ${hover} ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
