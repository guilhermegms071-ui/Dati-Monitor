import QRCode from 'qrcode';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';

import logo from '../../assets/logo-daticopy.svg';
import { ApiStatus } from '../../components/ApiStatus';
import { Button } from '../../components/ui/button';
import { Field, Input } from '../../components/ui/form';
import { Card } from '../../components/ui/primitives';
import { ApiError, api, unwrap } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { showError, showSuccess } from '../../lib/notify';
import { errorMessage } from '../../lib/utils';
import { product } from '../../product';

function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-5 p-4">
      <img src={logo} alt="Daticopy" className="h-28 w-auto" />
      <Card className="w-full max-w-sm p-7 shadow-lg">
        <p className="mb-1 text-sm font-semibold text-brand-600">{product.name}</p>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
        <div className="mt-5">{children}</div>
      </Card>
      {footer ? <div className="max-w-sm text-center text-xs">{footer}</div> : null}
    </div>
  );
}

function FormError({ message }: { message: string | null }) {
  return message ? (
    <p className="rounded-md bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300" role="alert">
      {message}
    </p>
  ) : null;
}

export function LoginPage() {
  const { login, status, limited } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === 'authenticated') return <Navigate to={limited ? '/sessao' : '/'} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password, needTotp ? totp : undefined);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setNeedTotp(true);
      } else {
        console.error('Login recusado:', err);
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Entrar" subtitle="Portal de monitoramento de impressoras" footer={<ApiStatus />}>
      <form className="flex flex-col gap-4" onSubmit={(e) => void submit(e)}>
        <Field label="E-mail" htmlFor="email">
          <Input
            id="email"
            type="text"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
            required
          />
        </Field>
        <Field label="Senha" htmlFor="password">
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
            required
          />
        </Field>
        {needTotp ? (
          <Field
            label="Código do autenticador"
            htmlFor="totp"
            hint="6 dígitos do aplicativo (Google Authenticator, Microsoft Authenticator…)"
          >
            <Input
              id="totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={totp}
              onChange={(e) => {
                setTotp(e.target.value.replace(/\D/g, ''));
              }}
              autoFocus
              required
            />
          </Field>
        ) : null}
        <FormError message={error} />
        <Button type="submit" loading={busy}>
          Entrar
        </Button>
        <Link to="/esqueci-senha" className="text-center text-sm text-brand-600 hover:underline">
          Esqueci a senha
        </Link>
      </form>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <AuthCard title="Esqueci a senha" subtitle="Enviaremos um link para redefinir a senha.">
      {sent ? (
        <div className="space-y-4 text-sm">
          <p>Se o e-mail estiver cadastrado, o link chega em alguns minutos (verifique também o spam).</p>
          <Link to="/login" className="text-brand-600 hover:underline">
            Voltar para o login
          </Link>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            unwrap(api.POST('/api/v1/auth/forgot-password', { body: { email } }))
              .then(() => {
                setSent(true);
              })
              .catch((err: unknown) => {
                showError(err, 'Não foi possível pedir o e-mail');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Field label="E-mail" htmlFor="email">
            <Input
              id="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
              }}
              required
            />
          </Field>
          <Button type="submit" loading={busy}>
            Enviar link
          </Button>
        </form>
      )}
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <AuthCard title="Nova senha" subtitle="Mínimo de 10 caracteres, diferente do e-mail.">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (password !== confirm) {
            setError('As senhas não conferem');
            return;
          }
          setBusy(true);
          setError(null);
          unwrap(api.POST('/api/v1/auth/reset-password', { body: { token, new_password: password } }))
            .then(() => {
              showSuccess('Senha redefinida. Entre com a nova senha.');
              void navigate('/login');
            })
            .catch((err: unknown) => {
              console.error(err);
              setError(errorMessage(err));
            })
            .finally(() => {
              setBusy(false);
            });
        }}
      >
        <Field label="Nova senha" htmlFor="new">
          <Input
            id="new"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
            required
          />
        </Field>
        <Field label="Repita a nova senha" htmlFor="confirm">
          <Input
            id="confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => {
              setConfirm(e.target.value);
            }}
            required
          />
        </Field>
        <FormError message={token ? error : 'Link inválido: abra o link do e-mail novamente.'} />
        <Button type="submit" loading={busy} disabled={!token}>
          Salvar
        </Button>
      </form>
    </AuthCard>
  );
}

export function ChangePasswordForm({ onDone }: { onDone: () => void }) {
  const { applySession } = useAuth();
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (password !== confirm) {
          setError('As senhas não conferem');
          return;
        }
        setBusy(true);
        setError(null);
        unwrap(
          api.POST('/api/v1/auth/change-password', { body: { current_password: current, new_password: password } }),
        )
          .then((resp) => {
            applySession(resp);
            showSuccess('Senha alterada');
            onDone();
          })
          .catch((err: unknown) => {
            console.error(err);
            setError(errorMessage(err));
          })
          .finally(() => {
            setBusy(false);
          });
      }}
    >
      <Field label="Senha atual" htmlFor="current">
        <Input
          id="current"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => {
            setCurrent(e.target.value);
          }}
          required
        />
      </Field>
      <Field label="Nova senha" htmlFor="new" hint="Mínimo de 10 caracteres, diferente do e-mail.">
        <Input
          id="new"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
          }}
          required
        />
      </Field>
      <Field label="Repita a nova senha" htmlFor="confirm">
        <Input
          id="confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value);
          }}
          required
        />
      </Field>
      <FormError message={error} />
      <Button type="submit" loading={busy}>
        Salvar nova senha
      </Button>
    </form>
  );
}

export function TotpSetupForm({ onDone }: { onDone: () => void }) {
  const { applySession } = useAuth();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    unwrap(api.POST('/api/v1/auth/totp/setup'))
      .then((s) => {
        setSetup(s);
        return QRCode.toDataURL(s.otpauth_uri, { margin: 1, width: 200 });
      })
      .then(setQr)
      .catch((err: unknown) => {
        showError(err, 'Não foi possível gerar o código do autenticador');
      });
  }, []);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        unwrap(api.POST('/api/v1/auth/totp/enable', { body: { code } }))
          .then((resp) => {
            applySession(resp);
            showSuccess('Autenticador ativado');
            onDone();
          })
          .catch((err: unknown) => {
            console.error(err);
            setError(errorMessage(err));
          })
          .finally(() => {
            setBusy(false);
          });
      }}
    >
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Leia o QR com o aplicativo autenticador (ou digite a chave) e informe o código de 6 dígitos.
      </p>
      {qr ? <img src={qr} alt="QR do autenticador" className="mx-auto h-48 w-48 rounded bg-white p-1" /> : null}
      {setup ? (
        <code className="block break-all rounded bg-slate-100 p-2 text-center text-xs dark:bg-slate-800">
          {setup.secret}
        </code>
      ) : null}
      <Field label="Código" htmlFor="code">
        <Input
          id="code"
          inputMode="numeric"
          maxLength={6}
          value={code}
          onChange={(e) => {
            setCode(e.target.value.replace(/\D/g, ''));
          }}
          required
        />
      </Field>
      <FormError message={error} />
      <Button type="submit" loading={busy} disabled={!setup}>
        Ativar
      </Button>
    </form>
  );
}

/** Sessão limitada: troca obrigatória de senha ou configuração obrigatória do TOTP. */
export function LimitedSessionPage() {
  const { limited, status, logout } = useAuth();
  if (status !== 'authenticated') return <Navigate to="/login" replace />;
  if (!limited) return <Navigate to="/" replace />;
  return limited === 'password_change_required' ? (
    <AuthCard
      title="Troque a senha"
      subtitle="É o primeiro acesso (ou a senha foi redefinida): defina uma senha só sua."
    >
      <ChangePasswordForm onDone={() => undefined} />
      <button
        type="button"
        className="mt-4 w-full text-center text-sm text-slate-500 hover:underline"
        onClick={() => void logout()}
      >
        Sair
      </button>
    </AuthCard>
  ) : (
    <AuthCard
      title="Ative o autenticador"
      subtitle="Sua revenda exige autenticação em duas etapas para administradores."
    >
      <TotpSetupForm onDone={() => undefined} />
    </AuthCard>
  );
}
