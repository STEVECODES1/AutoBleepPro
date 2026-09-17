import { spawn } from 'node:child_process';
import { BinScriptsAssetResolutionError } from './resolvers.js';

export interface AutoBleepCliOptions {
  /** Python interpreter; defaults to `python`. */
  python?: string;
  /** Path to AutoBleepPro's cli.py; defaults to `cli.py` on PATH/CWD. */
  scriptPath?: string;
  outputDir: string;
  method?: 'beep' | 'silence';
  srt?: boolean;
  txt?: boolean;
  sensitivity?: number;
  customWords?: string[];
  model?: string;
  trimSilence?: boolean;
}

export interface AutoBleepCliResult {
  outputVideo?: string;
  srt?: string;
  txt?: string;
  stdout: string;
  stderr: string;
}

/**
 * Parse AutoBleepPro CLI stdout. Progress goes to stderr, output paths to
 * stdout (one per line); the censored video has a video extension and the
 * `--srt`/`--txt` artifacts carry those extensions.
 */
export function parseAutoBleepCliOutput(stdout: string): Pick<AutoBleepCliResult, 'outputVideo' | 'srt' | 'txt'> {
  const out = {
    outputVideo: undefined as string | undefined,
    srt: undefined as string | undefined,
    txt: undefined as string | undefined,
  };
  for (const line of stdout.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)) {
    if (/\.srt$/i.test(line)) out.srt = line;
    else if (/\.txt$/i.test(line)) out.txt = line;
    else if (/\.(mp4|mov|avi|mkv|webm)$/i.test(line)) out.outputVideo = line;
  }
  return out;
}

export function buildAutoBleepArgs(inputPath: string, options: AutoBleepCliOptions): string[] {
  const args = [inputPath, '-o', options.outputDir];
  if (options.method) args.push('--method', options.method);
  if (options.srt) args.push('--srt');
  if (options.txt) args.push('--txt');
  if (options.sensitivity != null) args.push('--sensitivity', String(options.sensitivity));
  if (options.customWords?.length) args.push('--custom-words', options.customWords.join(','));
  if (options.model) args.push('--model', options.model);
  if (options.trimSilence) args.push('--trim-silence');
  return args;
}

/** Run `python cli.py <input> …` and resolve with the parsed output paths. */
export function runAutoBleepProCli(inputPath: string, options: AutoBleepCliOptions): Promise<AutoBleepCliResult> {
  return new Promise<AutoBleepCliResult>((resolve, reject) => {
    const python = options.python ?? 'python';
    const script = options.scriptPath ?? 'cli.py';
    const child = spawn(python, [script, ...buildAutoBleepArgs(inputPath, options)], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('error', (err) => reject(new BinScriptsAssetResolutionError(`AutoBleepPro failed to start: ${err.message}`)));
    child.on('close', (code) => {
      if (code !== 0) {
        reject(new BinScriptsAssetResolutionError(`AutoBleepPro exited ${code}: ${stderr.slice(-400)}`));
        return;
      }
      resolve({ ...parseAutoBleepCliOutput(stdout), stdout, stderr });
    });
  });
}
