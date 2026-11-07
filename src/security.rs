//! Password hashing, credential encryption and token helpers.

use aes_gcm::aead::{Aead, AeadCore, KeyInit, OsRng};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use anyhow::{Result, anyhow};
use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::Argon2;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use serde_json::Value;
use sha2::{Digest, Sha256};

pub fn hash_password(pw: &str) -> Result<String> {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(pw.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|e| anyhow!("hash failed: {e}"))
}

pub fn verify_password(pw: &str, hash: &str) -> bool {
    PasswordHash::new(hash)
        .map(|h| Argon2::default().verify_password(pw.as_bytes(), &h).is_ok())
        .unwrap_or(false)
}

pub fn sha256_hex(s: &str) -> String {
    hex::encode(Sha256::digest(s.as_bytes()))
}

/// 244 bits of OS randomness, URL safe.
pub fn random_token() -> String {
    format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple())
}

pub struct Cipher(Aes256Gcm);

impl Cipher {
    /// Any string works as key material; it is stretched with SHA-256.
    pub fn new(key: &str) -> Self {
        let k = Sha256::digest(key.as_bytes());
        Cipher(Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&k)))
    }

    pub fn encrypt(&self, v: &Value) -> Result<String> {
        let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
        let ct = self.0.encrypt(&nonce, v.to_string().as_bytes()).map_err(|_| anyhow!("encrypt failed"))?;
        let mut out = nonce.to_vec();
        out.extend(ct);
        Ok(B64.encode(out))
    }

    pub fn decrypt(&self, s: &str) -> Result<Value> {
        let raw = B64.decode(s)?;
        if raw.len() < 12 {
            return Err(anyhow!("ciphertext too short"));
        }
        let (n, ct) = raw.split_at(12);
        let pt = self
            .0
            .decrypt(Nonce::from_slice(n), ct)
            .map_err(|_| anyhow!("cannot decrypt credential (wrong encryption key?)"))?;
        Ok(serde_json::from_slice(&pt)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrips() {
        let c = Cipher::new("k");
        let v = serde_json::json!({"password": "secret"});
        assert_eq!(c.decrypt(&c.encrypt(&v).unwrap()).unwrap(), v);
        assert!(Cipher::new("other").decrypt(&c.encrypt(&v).unwrap()).is_err());
        let h = hash_password("pw").unwrap();
        assert!(verify_password("pw", &h) && !verify_password("nope", &h));
    }
}
