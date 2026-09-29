/**
 * Validate the CLI's installed direct dependencies against the workspace lock.
 *
 * npm workspaces may legitimately place packages either at `node_modules/` or
 * at `cli/node_modules/`. Node always prefers the nested location, so checking
 * only that a root React directory exists can miss an old nested package that
 * shadows the version recorded in `package-lock.json`.
 */

import {
  existsSync,
  readFileSync,
  realpathSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function installedPackageKey(frontendDir, dependencyName) {
  const candidates = [
    `cli/node_modules/${dependencyName}`,
    `node_modules/${dependencyName}`,
  ];
  return candidates.find((key) =>
    existsSync(join(frontendDir, key, "package.json"))
  );
}

function lockedPackageKey(lockPackages, dependencyName) {
  const candidates = [
    `cli/node_modules/${dependencyName}`,
    `node_modules/${dependencyName}`,
  ];
  return candidates.find((key) => lockPackages[key]);
}

function lockedVersion(lockPackages, packageRecord) {
  if (packageRecord.version) {
    return packageRecord.version;
  }
  if (packageRecord.link && packageRecord.resolved) {
    return lockPackages[packageRecord.resolved]?.version;
  }
  return undefined;
}

/**
 * Return human-readable dependency problems without mutating the installation.
 */
export function findCliDependencyIssues(frontendDir) {
  const root = resolve(frontendDir);
  const lockPath = join(root, "package-lock.json");
  if (!existsSync(lockPath)) {
    return [`missing workspace lockfile: ${lockPath}`];
  }

  let lock;
  try {
    lock = readJson(lockPath);
  } catch (error) {
    return [`cannot read workspace lockfile: ${error.message}`];
  }

  const lockPackages = lock.packages ?? {};
  const cliRecord = lockPackages.cli;
  if (!cliRecord) {
    return ["workspace lockfile does not contain the cli package"];
  }

  const dependencies = {
    ...(cliRecord.dependencies ?? {}),
    ...(cliRecord.devDependencies ?? {}),
  };
  const issues = [];

  for (const dependencyName of Object.keys(dependencies).sort()) {
    const expectedKey = lockedPackageKey(lockPackages, dependencyName);
    if (!expectedKey) {
      issues.push(`${dependencyName}: missing from workspace lockfile`);
      continue;
    }

    const actualKey = installedPackageKey(root, dependencyName);
    if (!actualKey) {
      issues.push(`${dependencyName}: not installed`);
      continue;
    }

    // A same-version nested package is still invalid when the lock expects the
    // hoisted package: Node would resolve the shadow copy and split identity.
    if (actualKey !== expectedKey) {
      issues.push(
        `${dependencyName}: installed at ${actualKey}, lockfile requires ${expectedKey}`
      );
      continue;
    }

    const packageRecord = lockPackages[expectedKey];
    const expectedVersion = lockedVersion(lockPackages, packageRecord);
    let actualPackage;
    try {
      actualPackage = readJson(join(root, actualKey, "package.json"));
    } catch (error) {
      issues.push(`${dependencyName}: cannot read installed package (${error.message})`);
      continue;
    }

    if (expectedVersion && actualPackage.version !== expectedVersion) {
      issues.push(
        `${dependencyName}: installed ${actualPackage.version ?? "unknown"}, ` +
          `lockfile requires ${expectedVersion}`
      );
      continue;
    }

    if (packageRecord.link && packageRecord.resolved) {
      const actualTarget = realpathSync(join(root, actualKey));
      const expectedTarget = realpathSync(join(root, packageRecord.resolved));
      if (actualTarget !== expectedTarget) {
        issues.push(
          `${dependencyName}: linked to ${actualTarget}, expected ${expectedTarget}`
        );
      }
    }
  }

  // Shared hooks must never resolve a private React copy, even though React is
  // an optional peer dependency and therefore absent from the CLI direct list.
  if (existsSync(join(root, "shared/node_modules/react/package.json"))) {
    issues.push("react: shared/node_modules contains a duplicate React installation");
  }

  return issues;
}

const modulePath = fileURLToPath(import.meta.url);
const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  const frontendDir = process.argv[2] ?? dirname(modulePath);
  const issues = findCliDependencyIssues(frontendDir);
  if (issues.length > 0) {
    console.error("Frontend CLI dependencies are stale:");
    for (const issue of issues) {
      console.error(`- ${issue}`);
    }
    process.exitCode = 1;
  }
}
