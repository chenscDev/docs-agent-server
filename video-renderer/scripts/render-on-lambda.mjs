/**
 * Remotion Lambda 渲染跳板（仅替换 render hop）。
 * 由 Python renderer 子进程调用；未配置 AWS / serveUrl 时直接退出非 0。
 *
 * 用法：
 *   node scripts/render-on-lambda.mjs \
 *     --composition TalkingCaptions \
 *     --props /path/to/props.json \
 *     --out /path/to/out.mp4
 *
 * 环境变量（与 Remotion 文档一致）：
 *   REMOTION_LAMBDA_REGION / AWS_REGION
 *   REMOTION_LAMBDA_FUNCTION_NAME
 *   REMOTION_LAMBDA_SERVE_URL
 *   AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
 *   （或 REMOTION_AWS_ACCESS_KEY_ID + REMOTION_AWS_SECRET_ACCESS_KEY）
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--') && i + 1 < argv.length) {
      out[a.slice(2)] = argv[++i];
    }
  }
  return out;
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const file = fs.createWriteStream(dest);
    mod
      .get(url, (res) => {
        if (
          res.statusCode &&
          res.statusCode >= 300 &&
          res.statusCode < 400 &&
          res.headers.location
        ) {
          file.close();
          fs.unlink(dest, () => {});
          downloadFile(res.headers.location, dest).then(resolve).catch(reject);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`下载失败 HTTP ${res.statusCode}`));
          return;
        }
        res.pipe(file);
        file.on('finish', () => file.close(() => resolve()));
      })
      .on('error', (err) => {
        fs.unlink(dest, () => {});
        reject(err);
      });
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const args = parseArgs(process.argv);
  const composition = args.composition || 'TalkingCaptions';
  const propsPath = args.props;
  const outPath = args.out;
  if (!propsPath || !outPath) {
    console.error('缺少 --props 或 --out');
    process.exit(2);
  }

  const region =
    process.env.REMOTION_LAMBDA_REGION ||
    process.env.AWS_REGION ||
    process.env.AWS_DEFAULT_REGION ||
    '';
  const functionName = process.env.REMOTION_LAMBDA_FUNCTION_NAME || '';
  const serveUrl = process.env.REMOTION_LAMBDA_SERVE_URL || '';
  if (!region || !functionName || !serveUrl) {
    console.error(
      '未配置 REMOTION_LAMBDA_REGION / FUNCTION_NAME / SERVE_URL，跳过 Lambda',
    );
    process.exit(3);
  }

  let renderMediaOnLambda;
  let getRenderProgress;
  try {
    // eslint-disable-next-line import/no-unresolved
    ({renderMediaOnLambda, getRenderProgress} = require('@remotion/lambda/client'));
  } catch (err) {
    console.error(
      '未安装 @remotion/lambda，请在 video-renderer 执行: npm i @remotion/lambda',
    );
    console.error(String(err && err.message ? err.message : err));
    process.exit(4);
  }

  const inputProps = JSON.parse(fs.readFileSync(propsPath, 'utf8'));
  console.log(
    JSON.stringify({
      stage: 'start',
      composition,
      region,
      functionName,
      serveUrl,
    }),
  );

  const {renderId, bucketName} = await renderMediaOnLambda({
    region,
    functionName,
    serveUrl,
    composition,
    inputProps,
    codec: 'h264',
    imageFormat: 'jpeg',
    maxRetries: 1,
    privacy: 'public',
    downloadBehavior: {
      type: 'download',
      fileName: path.basename(outPath) || 'out.mp4',
    },
  });

  const deadline = Date.now() + Number(process.env.REMOTION_LAMBDA_TIMEOUT_MS || 280000);
  let outputUrl = null;
  while (Date.now() < deadline) {
    const progress = await getRenderProgress({
      renderId,
      bucketName,
      functionName,
      region,
    });
    if (progress.fatalErrorEncountered) {
      const msg =
        (progress.errors && progress.errors[0] && progress.errors[0].message) ||
        'Lambda 渲染致命错误';
      throw new Error(msg);
    }
    if (progress.done) {
      outputUrl = progress.outputFile || progress.outKey || null;
      if (!outputUrl && progress.outputFile) {
        outputUrl = progress.outputFile;
      }
      // 部分版本字段为 outputFile 字符串 URL
      if (!outputUrl && typeof progress.outputFile === 'string') {
        outputUrl = progress.outputFile;
      }
      break;
    }
    await sleep(2000);
  }

  if (!outputUrl) {
    // 最后再拉一次
    const progress = await getRenderProgress({
      renderId,
      bucketName,
      functionName,
      region,
    });
    outputUrl = progress.outputFile || null;
  }
  if (!outputUrl || typeof outputUrl !== 'string') {
    throw new Error('Lambda 渲染完成但未返回可下载 URL');
  }

  fs.mkdirSync(path.dirname(outPath), {recursive: true});
  await downloadFile(outputUrl, outPath);
  if (!fs.existsSync(outPath) || fs.statSync(outPath).size <= 0) {
    throw new Error('下载成片为空');
  }
  console.log(JSON.stringify({stage: 'done', out: outPath, renderId, bucketName}));
}

main().catch((err) => {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
