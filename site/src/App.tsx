import { useEffect, useState } from "react";
import type { ReactNode } from "react";

const GITHUB_URL = "https://github.com/mangybyte/leashbyte";

const quickstartCommands = `git clone https://github.com/mangybyte/leashbyte.git
cd leashbyte
cp .env.example .env
openssl rand -hex 32
# Before you go on, put a different random value in each of the eight secrets in .env.
docker compose up --build -d --wait
uv sync --locked
uv run python examples/quickstart.py`;

const runCommands = `docker compose up --build -d --wait
uv sync --locked
uv run python examples/quickstart.py`;

type IconName =
  | "arrow"
  | "browser"
  | "check"
  | "copy"
  | "fetch"
  | "github"
  | "mcp"
  | "model"
  | "search"
  | "server"
  | "shield"
  | "spark";

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    browser: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></>,
    fetch: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></>,
    github: <path d="M20 12a8 8 0 1 1-2.3-5.6M20 12h-7M20 12v5"/>,
    mcp: <><path d="M8 9H5a3 3 0 0 0-3 3v0a3 3 0 0 0 3 3h3M16 9h3a3 3 0 0 1 3 3v0a3 3 0 0 1-3 3h-3M8 12h8"/></>,
    model: <><rect x="5" y="5" width="14" height="14" rx="3"/><path d="M9 9h6v6H9zM9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    server: <><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    spark: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8Z"/></>,
  };

  return (
    <svg
      aria-hidden="true"
      className="icon"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
    >
      {paths[name]}
    </svg>
  );
}

function Brand() {
  return (
    <a className="brand" href="#top" aria-label="LeashByte home">
      <span className="brand-mark" aria-hidden="true">L</span>
      <span>LeashByte</span>
    </a>
  );
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return reduced;
}

const terminalLines = [
  { text: "$ uv run python examples/quickstart.py", type: "command" },
  { text: "Fetch: 200 https://example.com/", type: "output" },
  { text: "Search: 3 results", type: "output" },
  { text: "Run: 031e429a...", type: "output" },
  { text: "State: succeeded", type: "success" },
];

function QuickstartTerminal() {
  const reducedMotion = useReducedMotion();
  const totalCharacters = terminalLines.reduce((sum, line) => sum + line.text.length, 0);
  const [typedCharacters, setTypedCharacters] = useState(reducedMotion ? totalCharacters : 0);

  useEffect(() => {
    if (reducedMotion) {
      setTypedCharacters(totalCharacters);
      return;
    }

    setTypedCharacters(0);
    const timer = window.setInterval(() => {
      setTypedCharacters((current) => {
        if (current >= totalCharacters) {
          window.clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, 22);
    return () => window.clearInterval(timer);
  }, [reducedMotion, totalCharacters]);

  return (
    <div className="terminal-shell">
      <div className="terminal-topbar">
        <div className="window-dots" aria-hidden="true"><i/><i/><i/></div>
        <span>quickstart.local</span>
        <span className="terminal-ready"><i/> SAMPLE</span>
      </div>
      <div className="terminal-body">
        <div className="terminal-meta">
          <span>EXAMPLE OUTPUT / LOCAL QUICKSTART</span>
          <span>UTF-8</span>
        </div>
        <p className="sr-only">{terminalLines.map((line) => line.text).join(". ")}</p>
        <div className="terminal-transcript" aria-hidden="true">
          {terminalLines.map((line, index) => {
            const start = terminalLines
              .slice(0, index)
              .reduce((sum, previous) => sum + previous.text.length, 0);
            const visible = line.text.slice(0, Math.max(0, Math.min(typedCharacters - start, line.text.length)));
            const isCurrent = typedCharacters >= start && typedCharacters < start + line.text.length;
            return (
              <div className={`terminal-line ${line.type}`} key={line.text}>
                <span>{visible || " "}</span>
                {isCurrent && <span className="terminal-cursor"/>}
              </div>
            );
          })}
          {typedCharacters >= totalCharacters && <span className="terminal-cursor final"/>}
        </div>
        <div className="terminal-ports">
          <span><b>REST</b> 127.0.0.1:8080</span>
          <span><b>MCP</b> 127.0.0.1:8081</span>
        </div>
      </div>
    </div>
  );
}

function Tile({
  children,
  className = "",
  icon,
  index,
  title,
}: {
  children: ReactNode;
  className?: string;
  icon: IconName;
  index: string;
  title: string;
}) {
  return (
    <article className={`tile ${className}`}>
      <div className="tile-heading">
        <span className="tile-icon"><Icon name={icon}/></span>
        <span className="tile-index">/{index}</span>
      </div>
      <h3>{title}</h3>
      <div className="tile-copy">{children}</div>
    </article>
  );
}

function CopyButton() {
  const [label, setLabel] = useState("Copy run commands");

  async function copyCommands() {
    try {
      await navigator.clipboard.writeText(runCommands);
      setLabel("Copied");
    } catch {
      setLabel("Copy failed");
    }
    window.setTimeout(() => setLabel("Copy run commands"), 2000);
  }

  return (
    <button className="copy-button" onClick={copyCommands} type="button">
      <Icon name={label === "Copied" ? "check" : "copy"} size={16}/>
      <span aria-live="polite">{label}</span>
    </button>
  );
}

function App() {
  return (
    <div className="site-shell" id="top">
      <header className="site-header">
        <Brand/>
        <nav className="main-nav" aria-label="Main navigation">
          <a href="#capabilities">Capabilities</a>
          <a href="#quickstart">Quickstart</a>
          <a href="#principles">Principles</a>
        </nav>
        <a className="github-link" href={GITHUB_URL} target="_blank" rel="noreferrer">
          <Icon name="github" size={18}/>
          <span>GitHub</span>
          <span className="external-arrow" aria-hidden="true">↗</span>
        </a>
      </header>

      <main>
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <div className="eyebrow"><span className="status-dot"/> SELF-HOSTED WEB INFRASTRUCTURE</div>
            <h1 id="hero-title">The web, under<br/>your <span>command.</span></h1>
            <p className="hero-intro">
              Search, fetch, browse and act through one API and one MCP server.
              Run LeashByte on your own systems. Connect the model you choose,
              including a local model.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#quickstart">Start building <Icon name="arrow" size={18}/></a>
              <a className="button button-secondary" href={GITHUB_URL} target="_blank" rel="noreferrer">
                <Icon name="github" size={18}/> View source
              </a>
            </div>
            <ul className="hero-facts" aria-label="Project facts">
              <li><Icon name="check" size={15}/> Apache-2.0</li>
              <li><Icon name="check" size={15}/> Self-hosted</li>
              <li><Icon name="check" size={15}/> Local models</li>
            </ul>
          </div>
          <QuickstartTerminal/>
        </section>

        <div className="capability-strip" role="group" aria-label="Core capabilities">
          <span>SEARCH</span><i/>
          <span>FETCH</span><i/>
          <span>BROWSER</span><i/>
          <span>AGENT</span><i/>
          <span>ONE API</span>
        </div>

        <section className="section" id="capabilities" aria-labelledby="capabilities-title">
          <div className="section-heading">
            <div>
              <p className="section-kicker">01 / CAPABILITIES</p>
              <h2 id="capabilities-title">From a question to<br/>a completed web task.</h2>
            </div>
            <p>Search, fetch, browser sessions and agent runs, all behind the same API.</p>
          </div>

          <div className="bento-grid">
            <Tile icon="search" index="01" title="Find the right source" className="lg:col-span-4">
              <p>Search through your own SearXNG service and get back titles, URLs and snippets in one consistent shape.</p>
              <div className="mini-search" aria-hidden="true"><Icon name="search" size={16}/><span>self-hosted web agents</span><kbd>↵</kbd></div>
            </Tile>

            <Tile icon="fetch" index="02" title="Read the useful part" className="lg:col-span-4">
              <p>Turn a page into Markdown, cleaned HTML or parsed JSON, with limits on time, size and redirects.</p>
              <div className="format-list" aria-hidden="true"><span>.md</span><span>.html</span><span>.json</span></div>
            </Tile>

            <Tile icon="browser" index="03" title="Work inside the page" className="md:col-span-2 lg:col-span-4">
              <p>Drive isolated Chrome sessions: click, type, select, scroll and navigate using numbered marks on the page.</p>
              <div className="browser-marks" aria-hidden="true"><span>[1] Search</span><span>[2] Sign in</span><span>[3] Next</span></div>
            </Tile>

            <Tile icon="spark" index="04" title="Give it a goal, keep every step" className="md:col-span-2 lg:col-span-7 agent-tile">
              <p>The agent reads the accessibility tree, acts on the page and records every accepted step. You set the limits on steps, time, tokens, navigations and run file size.</p>
              <div className="run-record" aria-hidden="true">
                <div className="run-record-top"><span>RUN / EXAMPLE</span><span className="run-success">● SUCCEEDED</span></div>
                <div className="run-flow"><span>OBSERVE</span><b>→</b><span>ACT</span><b>→</b><span>OBSERVE</span></div>
                <div className="run-record-bottom"><span>5 accepted steps</span><span>Limits active</span><span>Run files private</span></div>
              </div>
            </Tile>

            <Tile icon="mcp" index="05" title="One API and one MCP server" className="md:col-span-2 lg:col-span-5">
              <p>15 stable MCP tools cover search, fetch, browser sessions and agent runs, and the Python and TypeScript SDKs talk to the same API.</p>
              <div className="code-chip" aria-hidden="true"><span>await</span> client.fetchPage({"{"} url {"}"})</div>
            </Tile>

            <Tile icon="server" index="06" title="Runs on your own machines" className="md:col-span-2 lg:col-span-7">
              <p>Docker Compose starts the API, MCP server, browser, vault, run store and search service. Your credentials stay encrypted in the vault, and the model never sees them.</p>
              <div className="stack-list" aria-hidden="true">
                <span>API</span><span>MCP</span><span>CHROME</span><span>VAULT</span><span>POSTGRES</span><span>SEARXNG</span>
              </div>
            </Tile>

            <Tile icon="model" index="07" title="Bring your own model" className="md:col-span-2 lg:col-span-5">
              <p>Point it at any OpenAI-compatible local endpoint, or at Claude through the official Anthropic SDK. The agent worker runs alongside the rest of your stack.</p>
              <div className="model-status" aria-hidden="true"><span className="status-dot"/> MODEL ENDPOINT / YOUR CHOICE</div>
            </Tile>
          </div>
        </section>

        <section className="section quickstart-section" id="quickstart" aria-labelledby="quickstart-title">
          <div className="section-heading">
            <div>
              <p className="section-kicker">02 / QUICKSTART</p>
              <h2 id="quickstart-title">Your first run starts here.</h2>
            </div>
            <p>You'll need Docker with the Compose plugin, Python 3.13, uv and a local model endpoint.</p>
          </div>
          <div className="quickstart-panel">
            <div className="quickstart-code">
              <div className="code-topbar">
                <span>TERMINAL / BASH</span>
                <CopyButton/>
              </div>
              <pre><code>{quickstartCommands}</code></pre>
            </div>
            <ol className="quickstart-steps">
              <li><span>01</span><div><h3>Configure your stack</h3><p>Copy the example file, then generate a separate random value for each secret and paste it in.</p></div></li>
              <li><span>02</span><div><h3>Start the services</h3><p>Compose builds and starts everything. The API and MCP server listen on loopback by default.</p></div></li>
              <li><span>03</span><div><h3>Run the example</h3><p>The quickstart fetches a page, runs a search and completes an agent run on your local model.</p></div></li>
            </ol>
          </div>
          <p className="quickstart-note">The stack runs from this GitHub repository with Docker Compose. The Python SDK is on PyPI: <code>pip install leashbyte-sdk</code>.</p>
          <p className="quickstart-note">The quickstart agent run needs an OpenAI-compatible chat completions endpoint. <a href={`${GITHUB_URL}#quickstart`} target="_blank" rel="noreferrer">Read the full quickstart <span aria-hidden="true">↗</span></a></p>
        </section>

        <section className="section" id="principles" aria-labelledby="principles-title">
          <div className="section-heading compact">
            <div>
              <p className="section-kicker">03 / PRINCIPLES</p>
              <h2 id="principles-title">How it<br/>behaves.</h2>
            </div>
          </div>
          <div className="principle-grid">
            <article className="principle-card">
              <span className="principle-icon"><Icon name="shield" size={26}/></span>
              <div>
                <h3>Polite by default.</h3>
                <p>LeashByte checks robots.txt, rate-limits each host and supports Web Bot Auth out of the box. Plugins stay off until an administrator turns them on.</p>
                <p>LeashByte ships no CAPTCHA solver, fingerprint add-on or evasion plugin.</p>
              </div>
            </article>
            <article className="principle-card accent-card">
              <span className="principle-icon"><Icon name="fetch" size={26}/></span>
              <div>
                <h3>Open source, so you can check it.</h3>
                <p>LeashByte is licensed under the Apache License 2.0, so you can read the source and check the controls for yourself.</p>
                <a href={`${GITHUB_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer">Read the licence <Icon name="arrow" size={17}/></a>
              </div>
            </article>
          </div>
        </section>

        <section className="closing-section" aria-labelledby="closing-title">
          <div className="closing-prompt" aria-hidden="true">$ <span>your-next-run</span><i className="terminal-cursor"/></div>
          <h2 id="closing-title">Start your next web run.</h2>
          <p>Build agents that work on the web, on infrastructure you run yourself.</p>
          <a className="button button-primary" href={GITHUB_URL} target="_blank" rel="noreferrer">Explore LeashByte <Icon name="arrow" size={18}/></a>
        </section>
      </main>

      <footer className="site-footer">
        <div><Brand/><p>Self-hosted web infrastructure for agents.</p></div>
        <div className="footer-links">
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub ↗</a>
          <a href={`${GITHUB_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer">Apache-2.0 ↗</a>
          <a href="mailto:security@leashbyte.com">security@leashbyte.com</a>
        </div>
        <p className="copyright">© 2026 MangyByte Ltd.</p>
      </footer>
    </div>
  );
}

export default App;
