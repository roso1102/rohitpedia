# SUBJECT GUIDE: Next.js + UI Design
## Rohitpedia Engineering Standards

---

## Architecture decisions

### App Router (Next.js 14)
Use Server Components by default. Add `"use client"` only when you need: event handlers, browser APIs, React hooks (useState, useEffect), or real-time updates.

```typescript
// DEFAULT: Server Component (no directive needed)
// Fetches data server-side, no client JS bundle
export default async function ArticlePage({ params }: { params: { slug: string } }) {
    const article = await getArticle(params.slug)  // direct DB call, server-side
    return <ArticleReader article={article} />
}

// CLIENT: only when interaction required
"use client"
export default function TunnelReviewCard({ tunnel }: { tunnel: Tunnel }) {
    const [accepted, setAccepted] = useState(false)
    return <button onClick={() => accept(tunnel.id)}>Accept</button>
}
```

### Data fetching pattern
```typescript
// lib/db.ts — Prisma client with RLS
import { PrismaClient } from "@prisma/client"
import { getServerSession } from "next-auth"

export async function getDbWithRLS(): Promise<PrismaClient> {
    const session = await getServerSession()
    if (!session?.user?.id) throw new Error("Unauthenticated")

    // Set RLS context before any Prisma query
    await prisma.$executeRaw`SET LOCAL app.current_tenant = ${session.user.id}::uuid`
    return prisma
}

// Usage in Server Component
async function getArticle(slug: string) {
    const db = await getDbWithRLS()
    return db.articles.findUnique({ where: { slug } })
}
```

---

## Page structure

### Wiki article page (`/wiki/[slug]`)
```typescript
// app/wiki/[slug]/page.tsx
export default async function WikiArticlePage({ params }: Props) {
    const db = await getDbWithRLS()
    const [article, backlinks] = await Promise.all([
        db.articles.findUnique({ where: { slug: params.slug } }),
        db.backlinks.findMany({ where: { toSlug: params.slug }, take: 20 })
    ])

    if (!article) notFound()

    return (
        <div className="wiki-layout">
            <main>
                <h1>{article.title}</h1>
                <WikiRenderer markdown={article.body_md} />
            </main>
            <aside>
                <BacklinksPanel backlinks={backlinks} />
                <FacetsPanel facets={article.facets} />
                <ContextBadge context={article.context} />
            </aside>
        </div>
    )
}
```

### Wikilink renderer
```typescript
// components/WikiRenderer.tsx
import ReactMarkdown from "react-markdown"
import Link from "next/link"
import remarkGfm from "remark-gfm"

function WikiRenderer({ markdown }: { markdown: string }) {
    // Pre-process: convert [[slug]] to [slug](/wiki/slug) before markdown render
    const processed = markdown.replace(
        /\[\[([^\]]+)\]\]/g,
        (_, slug) => `[${slug}](/wiki/${slug})`
    )
    return (
        <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
                a: ({ href, children }) => (
                    href?.startsWith("/wiki/")
                        ? <Link href={href} className="wikilink">{children}</Link>
                        : <a href={href} target="_blank" rel="noopener">{children}</a>
                )
            }}
        >
            {processed}
        </ReactMarkdown>
    )
}
```

---

## Intelligence dashboard

### Tunnel review card
```typescript
// components/TunnelCard.tsx
"use client"

interface TunnelCardProps {
    tunnel: {
        id: string
        sourceSlug: string
        candidateSlug: string
        rnsScore: number
        tier: string
        rationale: string
        hopCount: number
    }
    onAccept: (id: string) => void
    onReject: (id: string) => void
}

export function TunnelCard({ tunnel, onAccept, onReject }: TunnelCardProps) {
    const [status, setStatus] = useState<"pending" | "accepted" | "rejected">("pending")

    const handleAccept = async () => {
        setStatus("accepted")
        onAccept(tunnel.id)
        // Copy wikilink to clipboard for manual insertion
        await navigator.clipboard.writeText(`[[${tunnel.candidateSlug}]]`)
        toast("Wikilink copied — paste it into the article")
    }

    const handleReject = async () => {
        setStatus("rejected")
        await fetch("/api/intelligence/reject-pair", {
            method: "POST",
            body: JSON.stringify({
                source_slug: tunnel.sourceSlug,
                candidate_slug: tunnel.candidateSlug
            })
        })
        onReject(tunnel.id)
    }

    if (status === "rejected") return null

    return (
        <div className={`tunnel-card tier-${tunnel.tier.toLowerCase()}`}>
            <div className="tunnel-pair">
                <ArticlePill slug={tunnel.sourceSlug} />
                <span className="arrow">↔</span>
                <ArticlePill slug={tunnel.candidateSlug} />
            </div>
            <p className="rationale">{tunnel.rationale}</p>
            <div className="meta">
                <span>RNS: {tunnel.rnsScore.toFixed(2)}</span>
                <span>{tunnel.hopCount} hop{tunnel.hopCount !== 1 ? "s" : ""}</span>
                <span className={`tier-badge ${tunnel.tier}`}>{tunnel.tier.toUpperCase()}</span>
            </div>
            {status === "accepted" ? (
                <p className="accepted-state">✓ Accepted — wikilink copied to clipboard</p>
            ) : (
                <div className="actions">
                    <button onClick={handleAccept} className="btn-accept">Accept + copy link</button>
                    <button onClick={handleReject} className="btn-reject">Reject · never again</button>
                </div>
            )}
        </div>
    )
}
```

---

## API routes

### Always validate inputs, return consistent errors
```typescript
// app/api/intelligence/reject-pair/route.ts
import { NextRequest, NextResponse } from "next/server"

const SLUG_PATTERN = /^[a-zA-Z0-9._/-]+$/

export async function POST(request: NextRequest) {
    const session = await getServerSession()
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

    const body = await request.json()
    const { source_slug, candidate_slug } = body

    // Validate inputs
    if (!SLUG_PATTERN.test(source_slug || "") || !SLUG_PATTERN.test(candidate_slug || "")) {
        return NextResponse.json({ error: "Invalid slug format" }, { status: 400 })
    }

    const db = await getDbWithRLS()

    // Check both directions (bidirectional rejection)
    await db.dislikePairs.upsert({
        where: { userId_slugA_slugB: {
            userId: session.user.id,
            slugA: source_slug,
            slugB: candidate_slug
        }},
        create: { userId: session.user.id, slugA: source_slug, slugB: candidate_slug },
        update: {}
    })

    // Also store reverse
    await db.dislikePairs.upsert({
        where: { userId_slugA_slugB: {
            userId: session.user.id,
            slugA: candidate_slug,
            slugB: source_slug
        }},
        create: { userId: session.user.id, slugA: candidate_slug, slugB: source_slug },
        update: {}
    })

    return NextResponse.json({ ok: true })
}
```

---

## UI design principles for this project

### Functional over decorative
This is a tool, not a portfolio. Every UI element exists to help the user act on information. No decorative elements that don't aid comprehension.

### Information density
The intelligence dashboard should show more information than typical consumer apps. Users are knowledge workers who can handle density. Don't dumb it down — but do organise it clearly.

### Mobile-first for reading and capture
Wiki article reading, tunnel review, and memory resurface should work well on mobile (Telegram users will often come from mobile). Capture UI must be mobile-friendly.

Desktop-first for: graph visualisation, semantic search with filters, facet editing.

### Dark mode
Support both light and dark. Use CSS variables consistently:
```css
/* globals.css */
:root {
    --bg-primary: #ffffff;
    --text-primary: #1a1917;
    --border: rgba(0, 0, 0, 0.1);
    --accent: #d4a843;
}

[data-theme="dark"] {
    --bg-primary: #0d0d0f;
    --text-primary: #e8e6e0;
    --border: rgba(255, 255, 255, 0.08);
    --accent: #d4a843;
}
```

### Typography
```css
/* Article reading — prioritise readability */
.wiki-body {
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size: 17px;
    line-height: 1.75;
    max-width: 680px;
}

/* UI chrome — clean sans */
.ui-text {
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 14px;
    line-height: 1.5;
}

/* Code and slugs */
.slug, code {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 12px;
}
```

---

## Common mistakes

```typescript
// MISTAKE 1: useEffect for data fetching in Server Component context
"use client"
export default function ArticlePage({ params }) {
    const [article, setArticle] = useState(null)
    useEffect(() => {
        fetch(`/api/articles/${params.slug}`).then(r => r.json()).then(setArticle)
    }, [])
    // FIX: Use Server Component with direct DB access

// MISTAKE 2: Not handling loading/error states in client components
export function TunnelList() {
    const [tunnels, setTunnels] = useState([])
    // Renders empty list on load — confusing
    // FIX: Add loading and error states, use Suspense

// MISTAKE 3: Exposing user_id in client-side code
const userId = session.user.id
fetch(`/api/articles?user_id=${userId}`)  // user_id in URL
// FIX: user_id comes from server session only, never from client

// MISTAKE 4: No error boundary around intelligence components
// If tunnel parsing fails, entire page crashes
// FIX: Wrap each intelligence section in <ErrorBoundary>
```
