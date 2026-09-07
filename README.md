# Agent IA HackAudit 2026

Application web sécurisée qui relie une entreprise cliente et son commissaire
aux comptes : le client dépose ses documents d'audit, le commissaire les
analyse avec l'aide d'un agent IA.

## 1. Connexion sécurisée

La connexion passe par **Amazon Cognito**, pas par un système maison :

- Le mot de passe n'est **jamais transmis en clair** (protocole SRP).
- Un **code à deux facteurs (MFA)** peut être exigé selon la configuration.
- Une fois connecté, l'application échange la session contre des
  **identifiants AWS temporaires** (valables ~1h, renouvelés automatiquement)
  — il n'y a **aucune clé AWS fixe** stockée nulle part dans l'app.

Ce que voit l'utilisateur après connexion dépend uniquement de son **groupe**
(client ou commissaire) — pas d'écran unique pour tout le monde.

## 2. Espace client — dépôt de documents

Un utilisateur rattaché à une entreprise cliente arrive directement sur une
page de dépôt :

- Il glisse ses fichiers (factures, bons de commande, etc.) — ils partent
  vers un espace de stockage (bucket S3) **réservé exclusivement à son
  entreprise**.
- Si un même utilisateur gère plusieurs entreprises, un menu déroulant lui
  permet de choisir dans laquelle déposer.
- La liste des documents déjà déposés s'affiche en dessous, pour vérifier
  ce qui a déjà été transmis.

**Cloisonnement réel, pas juste visuel** : l'isolation entre entreprises est
appliquée au niveau des permissions AWS elles-mêmes (pas seulement dans
l'interface) — un client ne peut techniquement pas accéder au bucket d'une
autre entreprise, même en contournant l'interface.

## 3. Espace commissaire — page d'analyse

Un utilisateur commissaire aux comptes arrive sur une interface de type chat :

1. Il choisit le **dossier client** à traiter dans un menu déroulant.
2. Les documents déjà déposés par ce client s'affichent.
3. Il pose sa demande en langage naturel, par exemple :
   - *« Fais-moi un rapport de pré-audit sur ce document. »*
   - *« Vérifie que la facture et le bon de commande correspondent bien
     (fournisseur, montant, articles), et signale tout oubli. »*

Derrière l'interface, deux étapes s'enchaînent **toujours dans le même
ordre**, gérées par l'application elle-même (pas laissées au hasard d'une
décision de l'IA) :

- **Extraction** : chaque document est lu tel quel (texte direct, ou OCR via
  Amazon Textract pour les PDF/images) — jamais deviné.
- **Vérification** : le contenu réellement extrait est envoyé à l'agent pour
  analyse, comparaison et détection d'anomalies.

Résultat : la réponse affichée s'appuie toujours sur le contenu réel des
documents déposés, jamais sur une supposition de l'IA.

## Configuration (pour lancer le projet)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# créer un fichier .env à la racine (voir clients.json pour la structure
# des espaces clients, et les variables COGNITO_*/AGENT_* pour l'accès AWS)
streamlit run app.py
```

Déploiement en production : voir `Dockerfile` / `docker-compose.yml`
(conteneurisé, pensé pour tourner derrière un reverse proxy avec TLS).

## Sécurité — en bref

- Aucune clé AWS statique : uniquement des identifiants temporaires liés à
  la session Cognito de chaque utilisateur.
- Un rôle IAM distinct par groupe d'utilisateurs, scoppé au strict
  nécessaire (un client ne peut toucher que son propre bucket).
- Les erreurs de connexion sont volontairement génériques pour éviter
  l'énumération de comptes.
- `.env` (secrets) n'est jamais versionné (voir `.gitignore`).
