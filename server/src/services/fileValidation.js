/**
 * File validation service - Magic byte / file signature checking
 *
 * Validates uploaded files by checking their actual binary content
 * rather than trusting client-provided MIME types or extensions.
 * This prevents disguised executables from being uploaded.
 */

import { ValidationError } from '../middleware/errorHandler.js';

/**
 * Known file signatures (magic bytes)
 *
 * Each entry maps a file type to one or more valid byte signatures.
 * offset: where in the file to check (default 0)
 * bytes: expected byte sequence as a Buffer
 */
const FILE_SIGNATURES = {
  // Images
  'image/jpeg': [
    { bytes: Buffer.from([0xFF, 0xD8, 0xFF]) },
  ],
  'image/png': [
    { bytes: Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) },
  ],
  'image/gif': [
    { bytes: Buffer.from('GIF87a', 'ascii') },
    { bytes: Buffer.from('GIF89a', 'ascii') },
  ],
  'image/webp': [
    // RIFF....WEBP - check RIFF at 0 and WEBP at 8
    { bytes: Buffer.from('RIFF', 'ascii'), also: { offset: 8, bytes: Buffer.from('WEBP', 'ascii') } },
  ],

  // Videos
  'video/mp4': [
    // ftyp box at offset 4 (standard ISO base media)
    { offset: 4, bytes: Buffer.from('ftyp', 'ascii') },
  ],
  'video/webm': [
    // EBML header (Matroska/WebM)
    { bytes: Buffer.from([0x1A, 0x45, 0xDF, 0xA3]) },
  ],
  'video/quicktime': [
    // MOV also uses ftyp or moov/mdat atoms
    { offset: 4, bytes: Buffer.from('ftyp', 'ascii') },
    { offset: 4, bytes: Buffer.from('moov', 'ascii') },
    { offset: 4, bytes: Buffer.from('mdat', 'ascii') },
    { offset: 4, bytes: Buffer.from('wide', 'ascii') },
    { offset: 4, bytes: Buffer.from('free', 'ascii') },
  ],

  // Archives
  'application/zip': [
    { bytes: Buffer.from([0x50, 0x4B, 0x03, 0x04]) }, // Standard ZIP
    { bytes: Buffer.from([0x50, 0x4B, 0x05, 0x06]) }, // Empty ZIP
  ],

  // CPIO (used by Houdini HDA files)
  'application/x-cpio': [
    { bytes: Buffer.from('070707', 'ascii') }, // Old ASCII format
    { bytes: Buffer.from('070701', 'ascii') }, // New ASCII format (newc)
    { bytes: Buffer.from('070702', 'ascii') }, // New CRC format
    { bytes: Buffer.from([0xC7, 0x71]) },      // Old binary format (little-endian)
    { bytes: Buffer.from([0x71, 0xC7]) },      // Old binary format (big-endian)
  ],
};

/**
 * HDA file extensions all use CPIO format
 */
const HDA_EXTENSIONS = ['.hda', '.hdanc', '.hdalc'];

/**
 * Check if a buffer matches a given file signature
 */
function matchesSignature(buffer, signature) {
  const offset = signature.offset || 0;

  // Buffer too small to contain the signature
  if (buffer.length < offset + signature.bytes.length) {
    return false;
  }

  const slice = buffer.subarray(offset, offset + signature.bytes.length);
  if (!slice.equals(signature.bytes)) {
    return false;
  }

  // Check secondary signature if present (e.g., RIFF+WEBP)
  if (signature.also) {
    const alsoOffset = signature.also.offset || 0;
    if (buffer.length < alsoOffset + signature.also.bytes.length) {
      return false;
    }
    const alsoSlice = buffer.subarray(alsoOffset, alsoOffset + signature.also.bytes.length);
    if (!alsoSlice.equals(signature.also.bytes)) {
      return false;
    }
  }

  return true;
}

/**
 * Validate a file buffer matches expected type based on magic bytes.
 *
 * @param {Buffer} buffer - File contents
 * @param {string} claimedMime - MIME type claimed by the client
 * @param {string} originalname - Original filename
 * @returns {boolean} true if valid
 * @throws {ValidationError} if file doesn't match expected type
 */
export function validateFileSignature(buffer, claimedMime, originalname) {
  if (!buffer || buffer.length < 4) {
    throw new ValidationError('File is empty or too small to validate');
  }

  const ext = originalname ? originalname.toLowerCase().split('.').pop() : '';

  // HDA files (.hda, .hdanc, .hdalc, .cpio) should be CPIO archives
  if (HDA_EXTENSIONS.includes(`.${ext}`) || ext === 'cpio') {
    const cpioSigs = FILE_SIGNATURES['application/x-cpio'];
    const valid = cpioSigs.some(sig => matchesSignature(buffer, sig));
    if (!valid) {
      throw new ValidationError(
        `File "${originalname}" does not appear to be a valid Houdini Digital Asset (expected CPIO archive format)`
      );
    }
    return true;
  }

  // ZIP files
  if (ext === 'zip') {
    const zipSigs = FILE_SIGNATURES['application/zip'];
    const valid = zipSigs.some(sig => matchesSignature(buffer, sig));
    if (!valid) {
      throw new ValidationError(
        `File "${originalname}" does not appear to be a valid ZIP archive`
      );
    }
    return true;
  }

  // Images and videos - check by claimed MIME type
  const signatures = FILE_SIGNATURES[claimedMime];
  if (signatures) {
    const valid = signatures.some(sig => matchesSignature(buffer, sig));
    if (!valid) {
      throw new ValidationError(
        `File "${originalname}" content does not match its claimed type (${claimedMime}). ` +
        `The file may be corrupted or disguised.`
      );
    }
    return true;
  }

  // No signature check available for this type - allow through
  // (JSON-based .sopdrop packages are validated separately by the validation service)
  return true;
}

/**
 * Express middleware factory: validate uploaded file(s) after multer processes them.
 *
 * Usage:
 *   router.post('/upload', multerMiddleware, validateUploads(), handler)
 *   router.post('/upload', multerMiddleware, validateUploads({ fields: ['thumbnail', 'file'] }), handler)
 *
 * @param {Object} options
 * @param {string[]} [options.fields] - Specific field names to validate (for .fields() uploads).
 *                                       If omitted, validates req.file and all files in req.files.
 * @param {boolean} [options.skipJson] - Skip validation for JSON files (.sopdrop, .json)
 */
export function validateUploads(options = {}) {
  const { fields, skipJson = true } = options;

  return (req, res, next) => {
    try {
      // Single file upload (multer .single())
      if (req.file) {
        const ext = req.file.originalname?.toLowerCase().split('.').pop() || '';
        if (skipJson && (ext === 'sopdrop' || ext === 'json')) {
          return next();
        }
        validateFileSignature(req.file.buffer, req.file.mimetype, req.file.originalname);
      }

      // Multiple files (multer .array() or .fields())
      if (req.files) {
        const filesToCheck = Array.isArray(req.files)
          ? req.files  // .array() returns an array
          : Object.entries(req.files) // .fields() returns { fieldname: [files] }
              .filter(([fieldname]) => !fields || fields.includes(fieldname))
              .flatMap(([, fileArr]) => fileArr);

        for (const file of filesToCheck) {
          const ext = file.originalname?.toLowerCase().split('.').pop() || '';
          if (skipJson && (ext === 'sopdrop' || ext === 'json')) {
            continue;
          }
          validateFileSignature(file.buffer, file.mimetype, file.originalname);
        }
      }

      next();
    } catch (err) {
      next(err);
    }
  };
}
