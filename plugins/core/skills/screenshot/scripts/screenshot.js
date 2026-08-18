#!/usr/bin/env node
// Workframe screenshot skill — 通用 HTML / URL 截图工具
// 详见 ../SKILL.md

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execSync } = require('child_process');

const BROWSER_PATHS = {
  win32: [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
  ],
  darwin: [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ],
  linux: [
    '/usr/bin/microsoft-edge',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ],
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--config') args.config = argv[++i];
    else if (a === '--output-dir') args.outputDir = argv[++i];
    else if (a === '--task-id') args.taskId = argv[++i];
    else if (a === '--keep-tmp') args.keepTmp = true;
  }
  if (!args.config) {
    console.error('用法：node screenshot.js --config <path/to/config.json> [--output-dir <dir>] [--task-id <id>]');
    process.exit(1);
  }
  return args;
}

function loadConfig(configPath) {
  const abs = path.resolve(configPath);
  if (!fs.existsSync(abs)) {
    throw new Error(`配置文件不存在: ${abs}`);
  }
  const raw = fs.readFileSync(abs, 'utf8');
  return JSON.parse(raw);
}

function findBrowser() {
  const candidates = BROWSER_PATHS[process.platform] || [];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function genTaskId(source) {
  // `YYYYMMDD-HHmmss`，与 SKILL.md §输出 声明的目录名格式一致。
  // 不能写成 replace(/[-:T]/g,'') 再 slice(0,15)——那样得到 `20260816072404.`：
  // 既丢了日期与时间之间的分隔符，又在末尾留一个点，而 Windows 会**静默吃掉**
  // 目录名末尾的点，产出的路径与代码里拼的对不上。
  const ts = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const hash = crypto.createHash('md5').update(`${source}|${Date.now()}`).digest('hex').slice(0, 8);
  return `${ts}-${hash}`;
}

function ensurePuppeteer() {
  try {
    return require('puppeteer-core');
  } catch (_) {}

  const cwd = process.cwd();
  const tmpDeps = path.join(cwd, 'tmp', 'screenshot-deps');
  const tmpNm = path.join(tmpDeps, 'node_modules', 'puppeteer-core');
  if (fs.existsSync(tmpNm)) {
    return require(tmpNm);
  }

  console.log('[screenshot] puppeteer-core 不可用，尝试在 tmp/screenshot-deps/ 自动安装...');
  fs.mkdirSync(tmpDeps, { recursive: true });
  const pkgPath = path.join(tmpDeps, 'package.json');
  if (!fs.existsSync(pkgPath)) {
    fs.writeFileSync(pkgPath, JSON.stringify({ name: 'screenshot-deps', private: true }, null, 2));
  }
  execSync('npm install puppeteer-core@^24 --no-audit --no-fund --silent', {
    cwd: tmpDeps,
    stdio: 'inherit',
  });
  return require(tmpNm);
}

function resolveSource(source) {
  if (/^https?:\/\//.test(source) || /^file:\/\//.test(source)) return source;
  const abs = path.resolve(source).replace(/\\/g, '/');
  return 'file:///' + encodeURI(abs);
}

async function runSetup(page, body) {
  if (!body || !body.trim()) return;
  const fn = new AsyncFunction('page', 'sleep', body);
  await fn(page, sleep);
}

async function getBoundingBox(page, selector) {
  return await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { width: Math.ceil(r.width), height: Math.ceil(r.height) };
  }, selector);
}

async function captureOne(page, cap, viewport, outputDir, navState) {
  const result = { name: cap.name, status: 'pending', path: null, error: null };

  try {
    // capture-level source 覆盖：与上一次不同则重新 goto（每个 Mermaid 块各自一个 HTML 时使用）
    if (cap.source) {
      const targetUrl = resolveSource(cap.source);
      if (targetUrl !== navState.currentUrl) {
        await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: cap.goto_timeout || 30000 });
        await sleep(navState.initialWaitMs);
        navState.currentUrl = targetUrl;
      }
    }
    if (cap.setup_js) await runSetup(page, cap.setup_js);
    if (cap.wait_selector) {
      await page.waitForSelector(cap.wait_selector, { timeout: cap.wait_timeout || 30000 });
    }
    if (cap.wait_ms) await sleep(cap.wait_ms);

    if (cap.fit_to_selector) {
      const box = await getBoundingBox(page, cap.fit_to_selector);
      if (box) {
        const newW = Math.max(viewport.width, box.width + 80);
        const newH = Math.max(viewport.height, box.height + 80);
        if (newW > viewport.width || newH > viewport.height) {
          await page.setViewport({
            width: newW,
            height: newH,
            deviceScaleFactor: viewport.deviceScaleFactor || 2,
          });
          await sleep(800);
        }
      }
    }

    const pngPath = path.join(outputDir, `${cap.name}.png`);
    if (cap.selector) {
      const el = await page.$(cap.selector);
      if (!el) throw new Error(`找不到 selector: ${cap.selector}`);
      await el.screenshot({ path: pngPath });
    } else {
      await page.screenshot({ path: pngPath, fullPage: true });
    }

    result.status = 'success';
    result.path = pngPath.replace(/\\/g, '/');
    console.log(`  ✓ ${cap.name} → ${pngPath}`);
  } catch (e) {
    result.status = 'error';
    result.error = e.message;
    console.error(`  ✗ ${cap.name}: ${e.message}`);
  }

  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const config = loadConfig(args.config);
  const taskIdSeed = config.source || (config.captures && config.captures[0] && config.captures[0].source) || 'no-source';
  const taskId = args.taskId || config.task_id || genTaskId(taskIdSeed);
  const outputDir = args.outputDir
    ? path.resolve(args.outputDir)
    : path.resolve('tmp', 'screenshots', taskId);
  fs.mkdirSync(outputDir, { recursive: true });

  const startedAt = new Date().toISOString();
  console.log(`[screenshot] task_id=${taskId} output=${outputDir}`);
  if (config.source) console.log(`[screenshot] default source=${config.source}`);

  const puppeteer = ensurePuppeteer();
  const executablePath = findBrowser();
  if (!executablePath) {
    console.warn('[screenshot] 未找到 Edge / Chrome；将尝试使用 puppeteer-core 自带 Chromium（若已下载）');
  }

  const viewport = Object.assign({ width: 1440, height: 900, deviceScaleFactor: 2 }, config.viewport || {});

  const browser = await puppeteer.launch({
    headless: 'new',
    ...(executablePath ? { executablePath } : {}),
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });

  const page = await browser.newPage();
  await page.setViewport(viewport);

  const initialWaitMs = config.initial_wait_ms || 500;
  const navState = { currentUrl: null, initialWaitMs };

  // 顶层 source（默认页面），可选；若所有 capture 都自带 source，可省略
  if (config.source) {
    const url = resolveSource(config.source);
    console.log(`[screenshot] 打开默认 ${url}`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: config.goto_timeout || 30000 });
    await sleep(initialWaitMs);
    navState.currentUrl = url;
  }

  const captures = [];
  const errors = [];
  for (const cap of config.captures || []) {
    if (!cap.source && !navState.currentUrl) {
      const msg = `capture "${cap.name}" 既无顶层 config.source，也无 cap.source`;
      console.error(`  ✗ ${msg}`);
      captures.push({ name: cap.name, status: 'error', path: null, error: msg });
      errors.push({ name: cap.name, error: msg });
      continue;
    }
    const r = await captureOne(page, cap, viewport, outputDir, navState);
    captures.push(r);
    if (r.status === 'error') errors.push({ name: cap.name, error: r.error });
  }

  await browser.close();
  const endedAt = new Date().toISOString();

  const manifest = {
    task_id: taskId,
    started_at: startedAt,
    ended_at: endedAt,
    source: config.source,
    output_dir: outputDir.replace(/\\/g, '/'),
    captures,
    errors,
  };
  const manifestPath = path.join(outputDir, '_manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  const logDir = path.resolve('.claude', 'workframe-state', 'logs', 'screenshot');
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, `${taskId}.json`);
  fs.writeFileSync(
    logPath,
    JSON.stringify(
      {
        __schema__: 'workframe.task-log.v1',
        task_id: taskId,
        skill: 'screenshot',
        status: errors.length ? (captures.some((c) => c.status === 'success') ? 'partial' : 'error') : 'success',
        started_at: startedAt,
        ended_at: endedAt,
        source: config.source,
        artifact_paths: captures.filter((c) => c.path).map((c) => c.path),
        error_summary: errors.length ? errors.map((e) => `${e.name}: ${e.error}`).join('; ') : null,
      },
      null,
      2
    )
  );

  console.log(`\n[screenshot] manifest → ${manifestPath}`);
  console.log(`[screenshot] log → ${logPath}`);

  if (errors.length) {
    console.error(`[screenshot] ${errors.length}/${captures.length} 失败`);
    process.exit(2);
  }
  console.log(`[screenshot] ✓ ${captures.length} 张全部成功`);
}

main().catch((e) => {
  console.error('[screenshot] 致命错误:', e);
  process.exit(1);
});
