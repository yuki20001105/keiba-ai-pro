import Link from 'next/link'

interface LogoProps {
  href?: string
  className?: string
}

export function Logo({ href = '/home', className = '' }: LogoProps) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 hover:opacity-70 transition-opacity ${className}`}
    >
      {/* モノグラムバッジ */}
      <span className="flex items-center justify-center w-7 h-7 bg-white rounded-sm shrink-0">
        <span
          className="text-black font-black text-[11px] tracking-tighter leading-none select-none"
          style={{ fontVariantLigatures: 'none' }}
        >
          TY
        </span>
      </span>
      {/* テキスト */}
      <span className="text-sm font-semibold tracking-tight text-white leading-none">
        競馬AI<span className="text-[#888] font-normal ml-0.5">Pro</span>
      </span>
    </Link>
  )
}
