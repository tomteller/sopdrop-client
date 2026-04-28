/**
 * Validation service for .sopdrop packages
 *
 * Validates JSON structure and metadata.
 * v2 format uses binary data (base64 encoded) instead of Python code.
 */

import { ValidationError } from '../middleware/errorHandler.js';

// Valid Houdini contexts
const VALID_CONTEXTS = [
  'sop', 'vop', 'dop', 'cop', 'cop2', 'top', 'lop', 'chop', 'obj', 'out', 'shop', 'rop', 'vex', 'unknown'
];

/**
 * Validate a .sopdrop package structure
 */
export function validateChopPackage(packageData) {
  let format = packageData.format || '';

  // Normalize legacy "chopsop-*" format names to "sopdrop-*"
  if (format.startsWith('chopsop-')) {
    format = format.replace('chopsop-', 'sopdrop-');
    packageData.format = format;
  }

  if (format === 'sopdrop-v1') {
    return validateV1Package(packageData);
  } else if (format === 'sopdrop-hda-v1') {
    return validateHdaPackage(packageData);
  } else if (format === 'sopdrop-vex-v1') {
    return validateVexPackage(packageData);
  } else if (format.startsWith('sopdrop-v')) {
    return validateV2Package(packageData);
  } else {
    throw new ValidationError(`Unknown format version: ${format}`);
  }
}

/**
 * Validate v1 package (code-based - legacy)
 */
function validateV1Package(packageData) {
  const errors = [];

  // Check required fields
  const requiredFields = ['format', 'context', 'metadata', 'code'];
  for (const field of requiredFields) {
    if (!(field in packageData)) {
      errors.push(`Missing required field: ${field}`);
    }
  }

  if (errors.length > 0) {
    throw new ValidationError(`Invalid .sopdrop package: ${errors.join(', ')}`);
  }

  // Validate context
  if (!VALID_CONTEXTS.includes(packageData.context)) {
    throw new ValidationError(
      `Invalid context: ${packageData.context}. Valid contexts: ${VALID_CONTEXTS.join(', ')}`
    );
  }

  // Validate metadata (node_count includes children, node_names is top-level)
  validateMetadata(packageData.metadata, false);

  // Validate code exists
  if (typeof packageData.code !== 'string' || packageData.code.trim().length === 0) {
    throw new ValidationError('Package code cannot be empty');
  }

  // Scan for dangerous code patterns
  scanForDangerousPatterns(packageData.code);

  return true;
}

/**
 * Validate v2 package (binary/cpio based)
 */
function validateV2Package(packageData) {
  const errors = [];

  // Check required fields for v2
  const requiredFields = ['format', 'context', 'metadata', 'data', 'checksum'];
  for (const field of requiredFields) {
    if (!(field in packageData)) {
      errors.push(`Missing required field: ${field}`);
    }
  }

  if (errors.length > 0) {
    throw new ValidationError(`Invalid .sopdrop package: ${errors.join(', ')}`);
  }

  // Validate context
  if (!VALID_CONTEXTS.includes(packageData.context)) {
    throw new ValidationError(
      `Invalid context: ${packageData.context}. Valid contexts: ${VALID_CONTEXTS.join(', ')}`
    );
  }

  // Validate metadata
  validateMetadata(packageData.metadata, false);

  // Validate data is base64
  if (typeof packageData.data !== 'string' || packageData.data.length === 0) {
    throw new ValidationError('Package data cannot be empty');
  }

  // Enforce size limit (200MB encoded = ~150MB decoded)
  const MAX_BASE64_LENGTH = 200 * 1024 * 1024;
  if (packageData.data.length > MAX_BASE64_LENGTH) {
    throw new ValidationError(`Package data too large (max ${Math.round(MAX_BASE64_LENGTH / 1024 / 1024)}MB encoded)`);
  }

  // Basic base64 validation
  if (!/^[A-Za-z0-9+/=]+$/.test(packageData.data)) {
    throw new ValidationError('Package data must be valid base64');
  }

  // Validate checksum format (SHA-256 = 64 hex chars)
  if (typeof packageData.checksum !== 'string' || !/^[a-f0-9]{64}$/.test(packageData.checksum)) {
    throw new ValidationError('Invalid checksum format');
  }

  return true;
}

/**
 * Validate VEX snippet package
 */
function validateVexPackage(packageData) {
  const errors = [];

  const requiredFields = ['format', 'context', 'metadata', 'code'];
  for (const field of requiredFields) {
    if (!(field in packageData)) {
      errors.push(`Missing required field: ${field}`);
    }
  }

  if (errors.length > 0) {
    throw new ValidationError(`Invalid VEX package: ${errors.join(', ')}`);
  }

  if (packageData.context !== 'vex') {
    throw new ValidationError(`VEX package must have context "vex", got "${packageData.context}"`);
  }

  const meta = packageData.metadata;
  if (typeof meta !== 'object' || meta === null) {
    throw new ValidationError('Metadata must be an object');
  }

  if (typeof packageData.code !== 'string' || packageData.code.trim().length === 0) {
    throw new ValidationError('VEX code cannot be empty');
  }

  // Enforce size limit (1MB for VEX snippets)
  if (packageData.code.length > 1024 * 1024) {
    throw new ValidationError('VEX code too large (max 1MB)');
  }

  return true;
}

/**
 * Validate metadata structure
 */
function validateMetadata(metadata, strictNodeCount = false) {
  if (typeof metadata !== 'object' || metadata === null) {
    throw new ValidationError('Metadata must be an object');
  }

  // Check required metadata fields
  if (typeof metadata.node_count !== 'number' || metadata.node_count < 0) {
    throw new ValidationError('metadata.node_count must be a non-negative number');
  }

  if (!Array.isArray(metadata.node_types)) {
    throw new ValidationError('metadata.node_types must be an array');
  }

  if (!Array.isArray(metadata.node_names)) {
    throw new ValidationError('metadata.node_names must be an array');
  }

  // For v1, node_count should match node_names length
  // For v2, node_count includes children, node_names is just top-level
  if (strictNodeCount && metadata.node_count !== metadata.node_names.length) {
    throw new ValidationError(
      `Metadata mismatch: node_count is ${metadata.node_count} but node_names has ${metadata.node_names.length} entries`
    );
  }

  // Optional fields type checking
  if ('has_hda_dependencies' in metadata && typeof metadata.has_hda_dependencies !== 'boolean') {
    throw new ValidationError('metadata.has_hda_dependencies must be a boolean');
  }

  if ('network_boxes' in metadata && typeof metadata.network_boxes !== 'number') {
    throw new ValidationError('metadata.network_boxes must be a number');
  }

  if ('sticky_notes' in metadata && typeof metadata.sticky_notes !== 'number') {
    throw new ValidationError('metadata.sticky_notes must be a number');
  }
}

/**
 * Validate HDA package (binary HDA file)
 */
function validateHdaPackage(packageData) {
  const errors = [];

  // Check required fields for HDA
  const requiredFields = ['format', 'asset_type', 'context', 'metadata', 'data', 'checksum'];
  for (const field of requiredFields) {
    if (!(field in packageData)) {
      errors.push(`Missing required field: ${field}`);
    }
  }

  if (errors.length > 0) {
    throw new ValidationError(`Invalid HDA package: ${errors.join(', ')}`);
  }

  // Validate asset_type
  if (packageData.asset_type !== 'hda') {
    throw new ValidationError(`Invalid asset_type for HDA package: ${packageData.asset_type}`);
  }

  // Validate context
  if (!VALID_CONTEXTS.includes(packageData.context)) {
    throw new ValidationError(
      `Invalid context: ${packageData.context}. Valid contexts: ${VALID_CONTEXTS.join(', ')}`
    );
  }

  // Validate HDA metadata
  const meta = packageData.metadata;
  if (typeof meta !== 'object' || meta === null) {
    throw new ValidationError('Metadata must be an object');
  }

  if (!meta.type_name || typeof meta.type_name !== 'string') {
    throw new ValidationError('HDA metadata.type_name is required');
  }

  if (!meta.file_name || typeof meta.file_name !== 'string') {
    throw new ValidationError('HDA metadata.file_name is required');
  }

  // Validate file extension
  const validExtensions = ['.hda', '.hdanc', '.hdalc', '.otl', '.otlnc', '.otllc'];
  const hasValidExt = validExtensions.some(ext => meta.file_name.toLowerCase().endsWith(ext));
  if (!hasValidExt) {
    throw new ValidationError(
      `Invalid HDA file extension. Valid extensions: ${validExtensions.join(', ')}`
    );
  }

  // Validate data is base64
  if (typeof packageData.data !== 'string' || packageData.data.length === 0) {
    throw new ValidationError('Package data cannot be empty');
  }

  // Enforce size limit (200MB encoded = ~150MB decoded)
  const MAX_HDA_BASE64_LENGTH = 200 * 1024 * 1024;
  if (packageData.data.length > MAX_HDA_BASE64_LENGTH) {
    throw new ValidationError(`HDA file too large (max ${Math.round(MAX_HDA_BASE64_LENGTH / 1024 / 1024)}MB encoded)`);
  }

  // Basic base64 validation
  if (!/^[A-Za-z0-9+/=]+$/.test(packageData.data)) {
    throw new ValidationError('Package data must be valid base64');
  }

  // Validate checksum format (SHA-256 = 64 hex chars)
  if (typeof packageData.checksum !== 'string' || !/^[a-f0-9]{64}$/.test(packageData.checksum)) {
    throw new ValidationError('Invalid checksum format');
  }

  return true;
}

// Patterns that indicate potentially malicious Python code
const DANGEROUS_PATTERNS = [
  { pattern: 'os.system', label: 'os.system' },
  { pattern: 'os.popen', label: 'os.popen' },
  { pattern: 'os.exec', label: 'os.exec' },
  { pattern: 'os.spawn', label: 'os.spawn' },
  { pattern: 'os.remove', label: 'os.remove' },
  { pattern: 'os.unlink', label: 'os.unlink' },
  { pattern: 'os.rmdir', label: 'os.rmdir' },
  { pattern: 'subprocess.', label: 'subprocess' },
  { pattern: '__import__', label: '__import__' },
  { pattern: '__builtins__', label: '__builtins__' },
  { pattern: '__subclasses__', label: '__subclasses__' },
  { pattern: 'importlib', label: 'importlib' },
  { pattern: /\beval\s*\(/, label: 'eval()' },
  { pattern: /\bexec\s*\(/, label: 'exec()' },
  { pattern: /\bcompile\s*\(/, label: 'compile()' },
  { pattern: /\bopen\s*\(/, label: 'open()' },
  { pattern: 'shutil.', label: 'shutil' },
  { pattern: 'socket.', label: 'socket' },
  { pattern: 'urllib', label: 'urllib' },
  { pattern: 'requests.', label: 'requests' },
  { pattern: 'http.client', label: 'http.client' },
  { pattern: 'ctypes', label: 'ctypes' },
  { pattern: 'pickle.loads', label: 'pickle.loads' },
  { pattern: /\bgetattr\s*\(/, label: 'getattr()' },
  { pattern: /\bsetattr\s*\(/, label: 'setattr()' },
  { pattern: /\bdelattr\s*\(/, label: 'delattr()' },
  { pattern: /\bglobals\s*\(/, label: 'globals()' },
  { pattern: /\blocals\s*\(/, label: 'locals()' },
];

/**
 * Strip string literals and comments from Python code so the dangerous-pattern
 * scanner only examines actual executable code, not parameter values or
 * HScript expressions embedded in strings (e.g. setExpression("eval(ch('x'))")).
 */
function stripStringsAndComments(code) {
  return code
    // Triple-quoted strings first, with optional prefix (r, f, b, u, rf, etc.)
    .replace(/[rRfFbBuU]{0,2}'''[\s\S]*?'''/g, '""')
    .replace(/[rRfFbBuU]{0,2}"""[\s\S]*?"""/g, '""')
    // Single and double quoted strings with optional prefix (handles escaped quotes)
    .replace(/[rRfFbBuU]{0,2}'(?:[^'\\]|\\.)*'/g, '""')
    .replace(/[rRfFbBuU]{0,2}"(?:[^"\\]|\\.)*"/g, '""')
    // Line comments
    .replace(/#.*/g, '');
}

/**
 * Scan Python code for dangerous patterns.
 * Strips string literals first so parameter expressions don't cause
 * false positives (e.g. Houdini's eval() HScript function).
 */
function scanForDangerousPatterns(code) {
  const stripped = stripStringsAndComments(code);
  const found = [];

  for (const { pattern, label } of DANGEROUS_PATTERNS) {
    if (pattern instanceof RegExp) {
      if (pattern.test(stripped)) {
        found.push(label);
      }
    } else {
      if (stripped.includes(pattern)) {
        found.push(label);
      }
    }
  }

  if (found.length > 0) {
    throw new ValidationError(
      `Package contains potentially unsafe code patterns: ${found.join(', ')}. ` +
      `Only Houdini node creation code (hou.* API) is allowed.`
    );
  }
}

/**
 * Validate dependencies array
 */
export function validateDependencies(dependencies) {
  if (!Array.isArray(dependencies)) {
    throw new ValidationError('Dependencies must be an array');
  }

  for (const dep of dependencies) {
    if (typeof dep !== 'object' || dep === null) {
      throw new ValidationError('Each dependency must be an object');
    }

    if (!dep.name || typeof dep.name !== 'string') {
      throw new ValidationError('Each dependency must have a name string');
    }
  }

  return true;
}

/**
 * Extract metadata from .sopdrop package for database storage
 */
export function extractAssetMetadata(packageData) {
  const meta = packageData.metadata || {};

  // Handle VEX packages
  if (packageData.format === 'sopdrop-vex-v1') {
    return {
      context: 'vex',
      houdiniVersion: packageData.houdini_version || null,
      assetType: 'vex',
      snippetType: meta.snippet_type || 'wrangle',
      lineCount: meta.line_count || 0,
      hasIncludes: meta.has_includes || false,
      // Compatibility fields
      nodeCount: 0,
      topLevelCount: 0,
      nodeTypes: [],
      nodeNames: [],
      hasHdaDependencies: false,
      networkBoxes: 0,
      stickyNotes: 0,
      dependencies: [],
      checksum: null,
      nodeGraph: null,
    };
  }

  // Handle HDA packages differently
  if (packageData.format === 'sopdrop-hda-v1') {
    return {
      context: packageData.context,
      houdiniVersion: packageData.houdini_version || null,
      assetType: 'hda',
      // HDA-specific metadata
      hdaTypeName: meta.type_name,
      hdaTypeLabel: meta.type_label || null,
      hdaVersion: meta.hda_version || null,
      hdaIcon: meta.icon || null,
      hdaFileName: meta.file_name,
      hdaFileSize: meta.file_size || 0,
      hdaHasHelp: meta.has_help || false,
      hdaSections: meta.sections || [],
      // For compatibility with snippet queries
      nodeCount: 1,
      topLevelCount: 1,
      nodeTypes: [meta.type_name],
      nodeNames: [meta.type_name],
      hasHdaDependencies: false,
      networkBoxes: 0,
      stickyNotes: 0,
      dependencies: [],
      checksum: packageData.checksum || null,
      nodeGraph: null,
    };
  }

  // Standard snippet package
  return {
    context: packageData.context,
    houdiniVersion: packageData.houdini_version || null,
    assetType: 'node',
    nodeCount: meta.node_count || 0,
    topLevelCount: meta.top_level_count || meta.node_names?.length || 0,
    nodeTypes: meta.node_types || [],
    nodeNames: meta.node_names || [],
    hasHdaDependencies: meta.has_hda_dependencies || false,
    networkBoxes: meta.network_boxes || 0,
    stickyNotes: meta.sticky_notes || 0,
    dependencies: packageData.dependencies || [],
    checksum: packageData.checksum || null,
    // Node graph data: { nodeName: { type, inputs: [inputNodeNames], outputs: [outputNodeNames] } }
    nodeGraph: meta.node_graph || null,
  };
}

export default {
  validateChopPackage,
  validateDependencies,
  extractAssetMetadata,
};
