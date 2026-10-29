// Build EchogramGL's library from the sibling checkout, pack it into web/vendor
// and install it.
//
// Usage: npm run pack:echogram [-- <path to AA-SI_EchogramGL/web>]
//
// The client installs aa-si-echogram-gl from the tarball this writes, so CI
// needs neither the sibling repo nor a registry. The tarball is installed by
// name: a plain npm install keeps the lockfile's old integrity, and npm's
// cached copy, while the version is unchanged. Commit package.json,
// package-lock.json and web/vendor after.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = fileURLToPath(new URL('..', import.meta.url));
const vendor = join(web, 'vendor');
const library = resolve(process.argv[2] ?? join(web, '..', '..', 'AA-SI_EchogramGL', 'web'));
const NAME = 'aa-si-echogram-gl';

function npm(args, cwd) {
  console.log(`> npm ${args.join(' ')}  (in ${cwd})`);
  const options = { cwd, stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8' };
  // npm is a .cmd script on Windows, which Node only runs through a shell.
  const result =
    process.platform === 'win32'
      ? spawnSync(['npm.cmd', ...args.map((arg) => (/\s/.test(arg) ? `"${arg}"` : arg))].join(' '), {
          ...options,
          shell: true,
        })
      : spawnSync('npm', args, options);
  if (result.status !== 0) {
    process.stdout.write(result.stdout ?? '');
    throw new Error(`npm ${args.join(' ')} failed with status ${result.status}`);
  }
  return result.stdout;
}

if (!existsSync(join(library, 'package.json'))) {
  throw new Error(`No EchogramGL web package at ${library}; pass its path as the first argument.`);
}
const { name, version } = JSON.parse(readFileSync(join(library, 'package.json'), 'utf8'));
if (name !== NAME) throw new Error(`${library} holds ${name}, not ${NAME}`);

if (!existsSync(join(library, 'node_modules'))) npm(['ci'], library);
mkdirSync(vendor, { recursive: true });
npm(['run', 'build:lib'], library);
const packed = npm(['pack', '--json', '--pack-destination', vendor], library);
const tarball = basename(JSON.parse(packed)[0].filename);

for (const file of readdirSync(vendor)) {
  if (file.startsWith(`${NAME}-`) && file.endsWith('.tgz') && file !== tarball) {
    rmSync(join(vendor, file));
    console.log(`removed vendor/${file}`);
  }
}

npm(['install', `${NAME}@file:vendor/${tarball}`], web);

console.log(`\nPacked ${NAME} ${version} into vendor/${tarball} and installed it.`);
console.log('Commit package.json, package-lock.json and web/vendor.');
