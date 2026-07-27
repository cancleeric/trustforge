import { Link } from 'react-router-dom'
import { useHermesI18n } from '../hermes/hermesI18n'

/**
 * 獨立子頁面的返回入口。
 *
 * 背景（2026-07-27，CEO 回報「這一頁進來要怎麼回去？為什麼會獨立在這裡？」）：
 * /asset-context、/eco-link、/peer-metrics 這幾支刻意不進 Header 主導覽的
 * 次要工具頁，是直接掛在 Routes 底下自帶版面的——沒有頂欄、沒有麵包屑，
 * 也沒有任何返回控制項。使用者從卡片連結進來之後，除了按瀏覽器上一頁或
 * 手改網址，畫面上沒有任何離開這頁的方法，等於是死路。
 *
 * 這裡補一個統一的返回入口。用 <Link> 而不是 history.back()：使用者可能是
 * 直接開網址或從外部連結進來的，那時候上一頁不是本站，back 會把人送出站外。
 */
export default function BackToBridgeLink() {
  const { t } = useHermesI18n()
  return (
    <Link
      to="/"
      className="inline-flex min-h-[24px] items-center self-start rounded font-mono text-xs text-tf-link transition hover:brightness-125"
    >
      {t('backToBridge')}
    </Link>
  )
}
