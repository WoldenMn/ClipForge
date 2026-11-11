import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

/* L'anneau de focus vient de :focus-visible (theme.css), commun a toute l'app.
   disabled:pointer-events-none neutralise le survol d'un bouton inactif. */
const SHELL =
  "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-control border font-semibold transition duration-150 ease-soft active:translate-y-px disabled:pointer-events-none disabled:opacity-45 disabled:shadow-none";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "border-transparent bg-accent text-on-accent shadow-card hover:bg-accent-hi hover:shadow-glow active:bg-accent-lo active:shadow-none",
  secondary:
    "border-accent bg-accent-soft text-accent-hi hover:bg-accent/20 hover:shadow-card active:bg-accent/25 active:shadow-none",
  ghost:
    "border-transparent text-ink-soft hover:bg-surface-hi hover:text-ink active:bg-surface",
  danger:
    "border-danger bg-danger-soft text-danger-ink hover:bg-danger-fill hover:text-on-danger hover:shadow-card active:bg-danger-fill active:shadow-none",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 px-3 text-sm",
  md: "h-10 px-5 text-[0.9375rem]",
};

function Spinner() {
  return (
    <svg
      className="size-4 shrink-0 animate-spin"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="8"
        cy="8"
        r="6.4"
        stroke="currentColor"
        strokeOpacity="0.3"
        strokeWidth="2.4"
      />
      <path
        d="M8 1.6a6.4 6.4 0 0 1 6.4 6.4"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Occupe la place de l'icone : la largeur du bouton ne bouge pas. */
  loading?: boolean;
  icon?: ReactNode;
  type?: "button" | "submit";
  className?: string;
}

export function Button({
  children,
  variant = "primary",
  size = "md",
  loading = false,
  icon,
  type = "button",
  disabled = false,
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type === "submit" ? "submit" : "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${SHELL} ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
}
