import type { SVGProps } from "react";

const AGENT_PALETTE = [
  "#2F6FED",
  "#F2811D",
  "#EE5A7A",
  "#6B9B1E",
  "#0E9AA7",
  "#5C6B8A",
] as const;
const INK = "#1D1A26";
const HEART_RED = "#CF3535";
const HEART_MUTED = "#D9D0BD";

/** The board's seeded PRNG, kept byte-for-byte equivalent in its arithmetic. */
function spriteHash(seed: string): () => number {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i += 1) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

type AgentAvatarProps = {
  id: string;
  size?: number;
  color?: string;
  className?: string;
};

/** Symmetric 7×7 pixel challenger sprite, seeded by the stable agent ID. */
export function AgentAvatar({
  id,
  size = 44,
  color,
  className,
}: AgentAvatarProps) {
  const random = spriteHash(id);
  const matrix: (boolean | "eye")[][] = [];
  const body = [0.35, 0.6, 0.9, 1];
  const top = [0.35, 0.15, 0.3, 0.1];
  const legs = [0.55, 0.45, 0.3, 0.15];

  for (let y = 0; y < 7; y += 1) {
    matrix[y] = [];
    for (let x = 0; x < 4; x += 1) {
      const probability = y === 0 ? top[x] : y >= 5 ? legs[x] : body[x];
      const pixel = random() < probability;
      matrix[y][x] = pixel;
      matrix[y][6 - x] = pixel;
    }
  }
  matrix[2][2] = matrix[2][4] = "eye";
  matrix[2][3] = true;
  matrix[3][2] = matrix[3][3] = matrix[3][4] = true;
  matrix[1][3] = true;

  const agentColor =
    color ?? AGENT_PALETTE[Math.floor(spriteHash(id)() * AGENT_PALETTE.length)];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 7 7"
      shapeRendering="crispEdges"
      className={className}
      style={{ display: "block", imageRendering: "pixelated" }}
      aria-hidden="true"
      focusable="false"
    >
      {matrix.flatMap((row, y) =>
        row.map((pixel, x) =>
          pixel ? (
            <rect
              key={`${x}-${y}`}
              x={x}
              y={y}
              width="1.02"
              height="1.02"
              fill={pixel === "eye" ? INK : agentColor}
            />
          ) : null,
        ),
      )}
    </svg>
  );
}

export type PixelIconName =
  "model" | "tool" | "trap" | "limit" | "answer" | "heart" | "start";

type PixelIconProps = {
  name: PixelIconName;
  size?: number;
  filled?: boolean;
  title?: string;
  className?: string;
} & Omit<SVGProps<SVGSVGElement>, "name" | "size" | "fill" | "title">;

const iconPaths: Record<Exclude<PixelIconName, "heart">, string> = {
  start:
    "M15 11h-2V9h2zm0 4h-2v-2h2zm-2 2h-2v-2h2zm0-8h-2V7h2zm-2-2H9V5h2zM9 21H7V3h2zm6-8h2v-2h-2zm-6 4h2v2H9z",
  model:
    "M5 3h14v2H5zm0 16h14v2H5zM3 5h2v14H3zm16 0h2v14h-2zM9 7h6v2H9zm0 8h6v2H9zM7 9h2v6H7zm8 0h2v6h-2zm-4-8h2v2h-2zm0 20h2v2h-2zM1 11h2v2H1zm20 0h2v2h-2zm0-4h2v2h-2zm0 8h2v2h-2zM1 15h2v2H1zm0-8h2v2H1zm6-6h2v2H7zm8 0h2v2h-2zm0 20h2v2h-2zm-8 0h2v2H7z",
  tool: "M22 22h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm-6-2H6v-2h8v2Zm4 0h-2v-2h2v2ZM6 16H4v-2h2v2Zm10 0h-2v-2h2v2ZM4 14H2V6h2v8Zm14 0h-2V6h2v8ZM6 6H4V4h2v2Zm10 0h-2V4h2v2Zm-2-2H6V2h8v2Z",
  trap: "M4 13h8v6h2v2h-2v2h-2v-8H2v-4h2v2Zm12 6h-2v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2v-2h2v2Zm-6-6h8v4h-2v-2h-8V5h-2V3h2V1h2v8Zm-8 2H4V9h2v2Zm2-2H6V7h2v2Zm2-2H8V5h2v2Z",
  limit:
    "M16 22H8v-2h8v2Zm-8-2H6v-4h2v4Zm10 0h-2v-4h2v4Zm-8-4H8v-2h2v2Zm6 0h-2v-2h2v2Zm-6-6h4v4h-4v-4Zm0 0H8V8h2v2Zm6 0h-2V8h2v2ZM8 8H6V4h2v4Zm10 0h-2V4h2v4Zm-2-4H8V2h8v2Z",
  answer:
    "M4 2h2v20H4z M4 4h16v2H4zm12 2h2v2h-2zm-2 2h2v2h-2zm2 2h2v2h-2zM4 12h16v2H4z",
};

const heartRows = [
  ".XX.XX.",
  "XXXXXXX",
  "XXXXXXX",
  ".XXXXX.",
  "..XXX..",
  "...X...",
];

/** Pixel icons from the arena board; untitled instances are decorative. */
export function PixelIcon({
  name,
  size = 16,
  filled = true,
  title,
  className,
  ...svgProps
}: PixelIconProps) {
  const heart = name === "heart";
  const heartFill = filled ? HEART_RED : HEART_MUTED;
  return (
    <svg
      width={size}
      height={size}
      viewBox={heart ? "0 0 7 6" : "0 0 24 24"}
      fill="currentColor"
      shapeRendering="crispEdges"
      className={className}
      aria-hidden={title ? undefined : "true"}
      role={title ? "img" : undefined}
      focusable="false"
      {...svgProps}
    >
      {title ? <title>{title}</title> : null}
      {heart ? (
        heartRows.flatMap((row, y) =>
          [...row].map((pixel, x) =>
            pixel === "X" ? (
              <rect
                key={`${x}-${y}`}
                x={x}
                y={y}
                width="1.02"
                height="1.02"
                fill={heartFill}
              />
            ) : null,
          ),
        )
      ) : (
        <path d={iconPaths[name as Exclude<PixelIconName, "heart">]} />
      )}
    </svg>
  );
}
