import Image from "next/image";
import { cn } from "@/lib/utils";

/** 元画像の余白を含めて表示し、実際の図柄がほぼ同じ大きさに見える倍率を指定します。 */
type ProviderLogo = { filename: string; displaySize: number };

/** 出典を確認して取り込んだ提供元ロゴです。Microsoftの各サービスは同じ企業マークを使います。 */
const providerLogos: Record<string, ProviderLogo> = {
  openai: { filename: "openai.svg", displaySize: 64 },
  deepseek: { filename: "deepseek.png", displaySize: 40 },
  mistral: { filename: "mistral.png", displaySize: 32 },
  xai: { filename: "xai.svg", displaySize: 32 },
  cohere: { filename: "cohere.svg", displaySize: 32 },
  "black-forest-labs": { filename: "black-forest-labs.svg", displaySize: 32 },
  "azure-ai-foundry": { filename: "microsoft.png", displaySize: 48 },
  "azure-ai-speech": { filename: "microsoft.png", displaySize: 48 },
  microsoft: { filename: "microsoft.png", displaySize: 48 },
};

/**
 * 各社のロゴを同じ大きさの白い枠へ表示し、提供元を視覚的に識別します。
 *
 * @remarks 素材の色・縦横比・形を変えず、出典はpublic/brand/providers/README.mdに記録します。
 * 元画像の余白の差は表示倍率で調整し、画像の切り抜きや図柄の変形は行いません。
 * 提供元名は隣接する見出しが伝えるため、装飾画像の名前を重ねて読み上げません。
 * 未登録の提供元には架空のロゴや別会社のマークを代用しません。
 */
export function ProviderMark({ providerId, className }: { providerId: string; className?: string }) {
  const logo = providerLogos[providerId];
  if (!logo) return null;
  return (
    <span aria-hidden="true" className={cn("relative inline-flex size-14 shrink-0 items-center justify-center rounded-2xl bg-white ring-1 ring-inset ring-slate-200", className)}>
      <Image src={`/brand/providers/${logo.filename}`} width={logo.displaySize} height={logo.displaySize} alt="" unoptimized style={{ width: logo.displaySize, height: logo.displaySize }} className="pointer-events-none absolute left-1/2 top-1/2 max-w-none -translate-x-1/2 -translate-y-1/2 object-contain" />
    </span>
  );
}
