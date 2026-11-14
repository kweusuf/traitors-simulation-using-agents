import { sigilFor } from '../lib/sigil';

interface Props {
  id: string;
  name?: string;
  size?: number;
  role?: 'faithful' | 'traitor';
  className?: string;
}

export default function Sigil({ id, name = id, size = 44, role, className = '' }: Props) {
  const spec = sigilFor(id, name);
  const ring =
    role === 'traitor'
      ? 'var(--color-traitor)'
      : role === 'faithful'
        ? 'var(--color-faithful)'
        : 'var(--border)';
  const [a, b] = spec.colors;
  const ticks = 3 + spec.tick;

  return (
    <span
      className={className}
      title={name}
      style={{
        width: size,
        height: size,
        display: 'inline-grid',
        placeItems: 'center',
        flex: 'none',
        filter: 'drop-shadow(0 4px 10px rgba(0,0,0,0.45))',
      }}
    >
      <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false">
        <circle cx="32" cy="32" r="31" fill="#0b0f0d" stroke={ring} strokeWidth="1.5" />
        <g transform={`rotate(${spec.rotation} 32 32)`}>
          {spec.shape === 0 && (
            <polygon
              points="32,6 54,19 54,45 32,58 10,45 10,19"
              fill="none"
              stroke={a}
              strokeWidth="1.4"
              opacity="0.9"
            />
          )}
          {spec.shape === 1 && (
            <polygon
              points="32,7 57,32 32,57 7,32"
              fill="none"
              stroke={a}
              strokeWidth="1.4"
              opacity="0.9"
            />
          )}
          {spec.shape === 2 && (
            <circle cx="32" cy="32" r="20" fill="none" stroke={a} strokeWidth="1.4" opacity="0.9" />
          )}
          {spec.shape === 3 && (
            <path
              d="M32 7 L32 57 M12 20 L52 44 M52 20 L12 44"
              fill="none"
              stroke={a}
              strokeWidth="1.2"
              opacity="0.75"
            />
          )}
          {Array.from({ length: ticks }).map((_, index) => (
            <circle
              key={index}
              cx={32 + Math.cos((index / ticks) * Math.PI * 2) * 24}
              cy={32 + Math.sin((index / ticks) * Math.PI * 2) * 24}
              r="1.4"
              fill={b}
            />
          ))}
        </g>
        <text
          x="32"
          y="38"
          textAnchor="middle"
          fontFamily="JetBrains Mono Variable, monospace"
          fontSize="13"
          fontWeight="600"
          fill="var(--color-parchment)"
          letterSpacing="0.5"
        >
          {spec.initials}
        </text>
      </svg>
    </span>
  );
}
